"""The host side of the ssh relay: accepting connection requests from the shim."""

import subprocess
import threading
import time

from .bridge import Bridge
from .errors import RequestRejected, SendboxError
from .teardown import stop_process

_LISTEN = 'exec 0<>"$1" && echo ready && exec cat'
_READY = b"ready\n"
_MAX_LINE_BYTES = 256


class SshRelay:
    """Serves every connection the container's shim asks for with a :class:`Bridge`.

    A single helper exec holds the session's ``requests`` FIFO open for reading
    and writing, so it never sees end-of-file, and streams the announced
    connection names to the host. At most ``max_connections`` are served at
    once, so a hostile container cannot flood the host with ssh processes.
    """

    def __init__(self, session, console, ssh_binary="ssh", max_connections=16):
        self._session = session
        self._console = console
        self._ssh_binary = ssh_binary
        self._slots = threading.BoundedSemaphore(max_connections)
        self._lock = threading.Lock()
        self._bridges = {}
        self._closing = False
        self._listener = None
        self._acceptor = None

    def start(self):
        """Attach to the request FIFO and start accepting connections in the background."""
        self._listener = self._session.client.spawn(
            self._session.container,
            ["sh", "-c", _LISTEN, "sendbox", self._session.requests],
            identity=self._session.identity,
            stdout=subprocess.PIPE,
        )
        if self._listener.stdout.readline() != _READY:
            raise SendboxError("could not start the ssh relay inside the container")
        self._acceptor = threading.Thread(target=self._accept, daemon=True)
        self._acceptor.start()

    def close(self, grace=2.0):
        """Stop accepting, give running connections ``grace`` seconds, then abort them."""
        with self._lock:
            self._closing = True
        stop_process(self._listener)
        deadline = time.monotonic() + grace
        for thread in self._running():
            thread.join(max(0.0, deadline - time.monotonic()))
        with self._lock:
            stragglers = list(self._bridges.items())
        for bridge, thread in stragglers:
            bridge.abort()
            thread.join(grace)

    def _accept(self):
        """Serve each connection name the shim announces until the listener stops."""
        stream = self._listener.stdout
        for line in iter(lambda: stream.readline(_MAX_LINE_BYTES), b""):
            self._slots.acquire()
            try:
                self._launch(line.decode("ascii", "replace").strip())
            except RequestRejected as exc:
                self._slots.release()
                self._console.notice(f"sendbox: ignored a relay request: {exc}")

    def _launch(self, name):
        """Start a bridge for connection ``name`` in its own thread."""
        bridge = Bridge(self._session, name, self._console, self._ssh_binary)
        thread = threading.Thread(target=self._serve, args=(bridge,), daemon=True)
        with self._lock:
            if self._closing:
                raise RequestRejected("the relay is shutting down")
            self._bridges[bridge] = thread
        thread.start()

    def _serve(self, bridge):
        """Run ``bridge`` and free its slot afterwards."""
        try:
            bridge.run()
        finally:
            with self._lock:
                self._bridges.pop(bridge, None)
            self._slots.release()

    def _running(self):
        """Threads of the connections still being served."""
        with self._lock:
            return list(self._bridges.values())
