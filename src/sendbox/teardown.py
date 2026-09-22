"""Guaranteed cleanup of everything a sendbox run creates.

The single most important property of sendbox is that nothing is ever left
behind: not on success, not on error, not on Ctrl-C, not on SIGTERM. A
:class:`Teardown` collects cleanup callbacks and runs them, newest first,
exactly once. The guarantee is enforced by three independent layers: the
context manager (normal and exceptional exit), an ``atexit`` hook (unexpected
interpreter shutdown) and signal handlers (SIGINT/SIGTERM/SIGHUP).

A signal does not clean up from inside its handler, where the interrupted code
may hold locks the cleanup needs (``Popen.wait`` does). The handler raises
:class:`Terminated` instead, so the stack unwinds like on any error, the
context manager cleans up with signals ignored, and the process then dies of
the signal it received.
"""

import atexit
import contextlib
import os
import signal
import subprocess
import sys
import threading

_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


class Terminated(BaseException):
    """Raised in the main thread when sendbox receives a terminating signal."""

    def __init__(self, signum):
        super().__init__(signum)
        self.signum = signum

    def die(self):
        """End the process by the received signal, as if it had never been caught."""
        signal.signal(self.signum, signal.SIG_DFL)
        os.kill(os.getpid(), self.signum)


class Teardown:
    """Cleanup callbacks that run exactly once, however the process ends."""

    _active = set()
    _lock = threading.RLock()
    _hooks_installed = False

    def __init__(self):
        self._callbacks = []

    def __enter__(self):
        self._install_global_hooks()
        with self._lock:
            type(self)._active.add(self)
        return self

    def __exit__(self, exc_type, exc, tb):
        with _signals_ignored():
            self.run()
        if isinstance(exc, Terminated):
            exc.die()
        return False

    def push(self, callback):
        """Register ``callback`` to run before every callback registered earlier."""
        with self._lock:
            self._callbacks.append(callback)

    def run(self):
        """Run all pending callbacks; a failing callback is reported, never raised."""
        while True:
            with self._lock:
                if not self._callbacks:
                    type(self)._active.discard(self)
                    return
                callback = self._callbacks.pop()
            try:
                callback()
            except Exception as exc:
                print(f"sendbox: warning: cleanup failed: {exc}", file=sys.stderr)

    @classmethod
    def _install_global_hooks(cls):
        """Register the atexit and signal handlers once per process."""
        if cls._hooks_installed:
            return
        cls._hooks_installed = True
        atexit.register(cls._run_all)
        for signum in _SIGNALS:
            signal.signal(signum, cls._handle_signal)

    @classmethod
    def _handle_signal(cls, received, frame):
        """Unwind into the active teardown, or simply die when there is none."""
        with cls._lock:
            active = bool(cls._active)
        if not active:
            Terminated(received).die()
            return
        for signum in _SIGNALS:
            signal.signal(signum, signal.SIG_IGN)
        raise Terminated(received)

    @classmethod
    def _run_all(cls):
        """Run every still-active teardown (idempotent)."""
        with cls._lock:
            active = list(cls._active)
        for teardown in active:
            teardown.run()


@contextlib.contextmanager
def _signals_ignored():
    """Ignore the terminating signals for the duration of the block."""
    previous = {signum: signal.signal(signum, signal.SIG_IGN) for signum in _SIGNALS}
    try:
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def stop_process(process, grace=5.0):
    """Terminate ``process``, killing it if it outlives ``grace`` seconds, and reap it."""
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
