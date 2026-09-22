"""The container-side footprint of one sendbox run.

A session is a private directory inside the container, owned by the user who
owns the repository. It holds the ssh shim that the container's git runs in
place of ssh, and the ``requests`` FIFO through which the shim asks the host for
a connection. Each connection gets its own ``c.XXXXXX`` subdirectory with an
``up`` FIFO (the shim's header, then git's outgoing bytes), a ``down`` FIFO
(ssh's output back to git) and, once ssh has finished, a ``status`` file with
its exit code. The session is created before git starts and removed after it
ends; nothing else in the container is touched.
"""

import posixpath
import re
import shlex
from importlib import resources

from .errors import RequestRejected, SendboxError
from .incus import Identity

_CONNECTION_NAME = re.compile(r"c\.[A-Za-z0-9]{6,32}")


def _script(name):
    """Return the text of a bundled container-side shell script."""
    return (resources.files(__package__) / "scripts" / name).read_text()


class ContainerSession:
    """A private directory inside the container hosting the ssh shim for one run."""

    def __init__(self, client, container, repo):
        self.client = client
        self.container = container
        self.repo = repo
        self.directory = None
        self.identity = None

    def open(self):
        """Create the session directory and learn which user owns the repository."""
        output = self.client.run_script(
            self.container, _script("setup.sh"), [self.repo], input=_script("shim.sh")
        )
        fields = output.split("\n")
        if len(fields) < 4 or not fields[1].isdigit() or not fields[2].isdigit():
            raise SendboxError(f"unexpected session setup output: {output!r}")
        self.directory = fields[0]
        self.identity = Identity(int(fields[1]), int(fields[2]), fields[3])

    def close(self):
        """Remove the session directory, waking anything still blocked on its FIFOs."""
        directory, self.directory = self.directory, None
        if directory is None:
            return
        try:
            self.client.run_script(self.container, _script("cleanup.sh"), [directory])
        except SendboxError as exc:
            raise SendboxError(
                f"could not remove {directory} inside '{self.container}': {exc}"
            ) from exc

    @property
    def ssh_command(self):
        """The ``GIT_SSH_COMMAND`` that makes the container's git use the shim."""
        return f"sh {shlex.quote(self._path('ssh'))}"

    @property
    def requests(self):
        """Path of the FIFO on which the shim announces new connections."""
        return self._path("requests")

    def connection(self, name):
        """Return the directory of the connection the shim announced as ``name``."""
        if not _CONNECTION_NAME.fullmatch(name):
            raise RequestRejected(f"malformed connection name {name[:40]!r}")
        return self._path(name)

    def _path(self, name):
        """Path of ``name`` inside the session directory."""
        return posixpath.join(self.directory, name)
