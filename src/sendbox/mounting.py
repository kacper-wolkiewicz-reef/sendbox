"""Mounting a container repository via ``incus file mount`` (sshfs).

The single most important property of sendbox is that a mount must NEVER be
left behind: not on success, not on error, not on Ctrl-C, not on SIGTERM.

``incus file mount`` is a foreground, blocking command (it prints "Press ctrl+c
to finish" and holds an sshfs mount until interrupted). :class:`IncusFileMount`
therefore launches it as a background process, waits for the mount to become
ready, and on teardown signals it to unmount cleanly — backed by an explicit
``fusermount``/``umount`` fallback. The guarantee is enforced by three
independent layers: a ``try/finally`` (normal and exceptional exit), an
``atexit`` hook (unexpected interpreter shutdown) and signal handlers
(SIGINT/SIGTERM/SIGHUP).
"""

import atexit
import os
import signal
import subprocess
import tempfile
import threading
import time

from .errors import SendboxError


class IncusFileMount:
    """An ``incus file mount`` of a container path, guaranteed to be unmounted."""

    _active = set()
    _lock = threading.Lock()
    _hooks_installed = False

    def __init__(self, source, binary="incus", timeout=30.0, poll_interval=0.1):
        self._source = source
        self._binary = binary
        self._timeout = timeout
        self._poll_interval = poll_interval
        self._target = None
        self._proc = None

    def __enter__(self):
        self._install_global_hooks()
        self._target = tempfile.mkdtemp(prefix="sendbox-")
        with self._lock:
            type(self)._active.add(self)
        try:
            self._proc = subprocess.Popen(
                [self._binary, "file", "mount", self._source, self._target],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            self._wait_until_mounted()
        except BaseException:
            self._teardown()
            raise
        return self._target

    def __exit__(self, exc_type, exc, tb):
        self._teardown()
        return False

    def _wait_until_mounted(self):
        """Block until the mountpoint is live, or raise if the mount never comes up."""
        deadline = time.monotonic() + self._timeout
        while True:
            if os.path.ismount(self._target):
                return
            if self._proc.poll() is not None:
                detail = (self._proc.stderr.read() or "").strip()
                raise SendboxError(
                    f"incus file mount failed: {detail or 'process exited early'}"
                )
            if time.monotonic() >= deadline:
                raise SendboxError("timed out waiting for the mount to become ready")
            time.sleep(self._poll_interval)

    def _teardown(self):
        """Stop the mount process and ensure the mountpoint is unmounted and removed."""
        self._stop_process()
        if self._target:
            self._force_unmount(self._target)
            if os.path.isdir(self._target):
                try:
                    os.rmdir(self._target)
                except OSError:
                    pass
            self._target = None
        with self._lock:
            type(self)._active.discard(self)

    def _stop_process(self):
        """Signal the foreground mount process to unmount, escalating if it lingers."""
        proc = self._proc
        self._proc = None
        if proc is None or proc.poll() is not None:
            return
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=10)
            return
        except subprocess.TimeoutExpired:
            pass
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    @staticmethod
    def _force_unmount(target):
        """Best-effort fallback unmount in case the process did not clean up."""
        if not os.path.ismount(target):
            return
        fallbacks = (
            ["fusermount", "-u", target],
            ["fusermount3", "-u", target],
            ["fusermount", "-uz", target],
            ["umount", "-l", target],
        )
        for command in fallbacks:
            subprocess.run(command, capture_output=True, text=True)
            if not os.path.ismount(target):
                return

    @classmethod
    def _install_global_hooks(cls):
        """Register atexit and signal handlers that flush every active mount once."""
        if cls._hooks_installed:
            return
        cls._hooks_installed = True
        atexit.register(cls._cleanup_all)
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(signum, cls._make_signal_handler())

    @classmethod
    def _make_signal_handler(cls):
        """Build a handler that tears down mounts, then re-raises the signal."""

        def handler(received, frame):
            cls._cleanup_all()
            signal.signal(received, signal.SIG_DFL)
            os.kill(os.getpid(), received)

        return handler

    @classmethod
    def _cleanup_all(cls):
        """Tear down every still-active mount (idempotent)."""
        with cls._lock:
            active = list(cls._active)
        for mount in active:
            mount._teardown()
