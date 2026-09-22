"""Running the user's git command inside the container."""

import subprocess

from .console import TerminalState
from .errors import SendboxError
from .teardown import stop_process


class ContainerGit:
    """The user's git command, run in the repository as its owner.

    ``GIT_SSH_COMMAND`` points git at the session's shim, so every ssh transport
    goes through the host relay; ``GIT_SSH_VARIANT`` tells git to pass OpenSSH
    style arguments without probing the command first.
    """

    def __init__(self, session):
        self._session = session
        self._process = None
        self._terminal = None

    def run(self, git_args):
        """Run git with ``git_args`` in the foreground, returning its exit code."""
        session = self._session
        argv = session.client.exec_command(
            session.container,
            ["git", *git_args],
            identity=session.identity,
            cwd=session.repo,
            env={"GIT_SSH_COMMAND": session.ssh_command, "GIT_SSH_VARIANT": "ssh"},
        )
        self._terminal = TerminalState.capture()
        try:
            self._process = subprocess.Popen(argv)
        except OSError as exc:
            raise SendboxError(f"failed to execute incus: {exc}") from exc
        return self._process.wait()

    def stop(self):
        """Stop git if it still runs and leave the host terminal as it was found."""
        stop_process(self._process, grace=10.0)
        if self._terminal is not None:
            self._terminal.restore()
