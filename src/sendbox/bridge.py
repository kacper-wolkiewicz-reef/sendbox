"""Relaying one ssh connection between the container's shim and the host's ssh."""

import os
import subprocess
import threading

from .errors import RequestRejected, SendboxError
from .ssh_request import MAX_HEADER_BYTES, parse_header
from .teardown import stop_process

_CHUNK_BYTES = 65536
_WRITE_FIFO = 'exec cat > "$1"'
_WRITE_STATUS = 'printf "%s\\n" "$2" > "$1"'
_SSH_FAILED_HINT = (
    "sendbox: ssh failed on the host; it runs with BatchMode=yes, so keys must be "
    "loaded into ssh-agent and the server's host key must already be in known_hosts"
)


class Bridge:
    """One connection requested by the shim, served by a host ssh process.

    Two helper execs attach to the connection's FIFOs: ``up`` delivers the
    request header followed by git's outgoing bytes, ``down`` carries ssh's
    output back to git. ssh's exit code is written to the ``status`` file
    *before* ``down`` is closed, so the shim always finds it once its input ends.
    """

    def __init__(self, session, name, console, ssh_binary="ssh"):
        self._session = session
        self._path = session.connection(name)
        self._console = console
        self._ssh_binary = ssh_binary
        self._processes = []
        self._lock = threading.Lock()
        self._aborted = False

    def run(self):
        """Serve the connection to its end, reporting failures on the console."""
        try:
            self._serve()
        except SendboxError as exc:
            if not self._aborted:
                self._console.notice(f"sendbox: ssh relay error: {exc}")
        finally:
            self._stop_all()

    def abort(self):
        """Tear the connection down immediately, without waiting for either side."""
        with self._lock:
            self._aborted = True
        self._stop_all()

    def _serve(self):
        """Attach to the FIFOs, relay the request and hand the shim its exit code."""
        up = self._spawn(["cat", f"{self._path}/up"], stdout=subprocess.PIPE)
        down = self._spawn(
            ["sh", "-c", _WRITE_FIFO, "sendbox", f"{self._path}/down"],
            stdin=subprocess.PIPE,
        )
        try:
            request = parse_header(up.stdout.readline(MAX_HEADER_BYTES + 1))
        except RequestRejected as exc:
            self._console.notice(f"sendbox: refused an ssh request from the container: {exc}")
            code = 255
        else:
            code = self._relay(request, up.stdout, down.stdin)
        if not self._aborted:
            self._write_status(code)
        _close(down.stdin)
        down.wait()

    def _relay(self, request, upstream, downstream):
        """Run host ssh for ``request``, pumping bytes both ways; return its exit code."""
        self._console.notice(f"sendbox: ssh {request.describe()}")
        try:
            ssh = subprocess.Popen(
                request.argv(self._ssh_binary),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=request.environment(os.environ),
                start_new_session=True,
            )
        except OSError as exc:
            self._console.notice(f"sendbox: cannot run ssh on the host: {exc}")
            return 255
        self._track(ssh)
        self._console.relay(ssh.stderr)
        _start_pump(upstream, ssh.stdin, close_sink=True)
        receiver = _start_pump(ssh.stdout, downstream, close_sink=False)
        code = ssh.wait()
        receiver.join()
        if code == 255 and not self._aborted:
            self._console.notice(_SSH_FAILED_HINT)
        return code if 0 <= code <= 255 else 255

    def _write_status(self, code):
        """Leave ssh's exit code where the shim reads it after its input ends."""
        self._session.client.run_script(
            self._session.container,
            _WRITE_STATUS,
            [f"{self._path}/status", str(code)],
            identity=self._session.identity,
        )

    def _spawn(self, command, **streams):
        """Start a helper exec bound to this connection, running as the repository owner."""
        process = self._session.client.spawn(
            self._session.container, command, identity=self._session.identity, **streams
        )
        self._track(process)
        return process

    def _track(self, process):
        """Remember ``process`` for teardown, stopping it at once if already aborted."""
        with self._lock:
            self._processes.append(process)
            aborted = self._aborted
        if aborted:
            stop_process(process)
            raise SendboxError("connection aborted")

    def _stop_all(self):
        """Stop every process this connection started (idempotent)."""
        with self._lock:
            processes = list(self._processes)
        for process in processes:
            stop_process(process)


def _start_pump(source, sink, close_sink):
    """Copy ``source`` to ``sink`` in a background thread as data arrives."""
    thread = threading.Thread(target=_pump, args=(source, sink, close_sink), daemon=True)
    thread.start()
    return thread


def _pump(source, sink, close_sink):
    """Forward every chunk of ``source`` to ``sink`` until either side gives out."""
    try:
        while True:
            chunk = source.read1(_CHUNK_BYTES)
            if not chunk:
                break
            sink.write(chunk)
            sink.flush()
    except (OSError, ValueError):
        pass
    finally:
        if close_sink:
            _close(sink)


def _close(stream):
    """Close ``stream``, ignoring a peer that has already gone away."""
    try:
        stream.close()
    except (OSError, ValueError):
        pass
