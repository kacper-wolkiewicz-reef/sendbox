"""The host terminal while incus drives it.

An interactive ``incus exec`` switches the host terminal to raw mode, where a
bare newline no longer returns the carriage, so sendbox's own messages would
drift to the right. :class:`Console` writes whole, sanitized lines ending in
``\\r\\n`` on a terminal; :class:`TerminalState` puts the terminal back if the
incus client dies before restoring it itself.
"""

import sys
import termios
import threading


class Console:
    """Serialized writer for sendbox's own messages on stderr."""

    def __init__(self, stream=None):
        self._stream = stream or sys.stderr
        self._lock = threading.Lock()
        self._newline = "\r\n" if self._stream.isatty() else "\n"

    def notice(self, message):
        """Write ``message``, stripped of control characters, one line at a time."""
        lines = [_printable(line) for line in message.splitlines()]
        text = "".join(line + self._newline for line in lines)
        with self._lock:
            self._stream.write(text)
            self._stream.flush()

    def relay(self, stream):
        """Copy a byte stream (such as ssh's stderr) to the console in the background."""
        thread = threading.Thread(target=self._copy_lines, args=(stream,), daemon=True)
        thread.start()
        return thread

    def _copy_lines(self, stream):
        """Forward every line of ``stream`` until it closes."""
        for raw in iter(stream.readline, b""):
            self.notice(raw.decode("utf-8", "replace"))


class TerminalState:
    """A snapshot of the host terminal's mode that can be restored later."""

    def __init__(self, fd, attributes):
        self._fd = fd
        self._attributes = attributes

    @classmethod
    def capture(cls, stream=None):
        """Snapshot ``stream``'s terminal (stdin by default); a no-op without one."""
        stream = stream or sys.stdin
        if stream is None or not stream.isatty():
            return cls(None, None)
        fd = stream.fileno()
        return cls(fd, termios.tcgetattr(fd))

    def restore(self):
        """Put the terminal back into the captured mode."""
        if self._attributes is None:
            return
        try:
            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._attributes)
        except termios.error:
            pass


def _printable(line):
    """Drop characters that could drive the terminal (escape sequences and the like)."""
    return "".join(char for char in line if char.isprintable() or char == "\t")
