"""Interaction with the incus CLI.

This module isolates every shell-out to ``incus`` so the rest of sendbox can
reason about containers in terms of plain Python values.
"""

import json
import shutil
import subprocess
from dataclasses import dataclass

from .errors import SendboxError

_PRUNED_PATHS = ("/proc", "/sys", "/dev", "/run")
_PRUNED_DIR_NAMES = (".cache", "node_modules", ".venv", "venv", ".tox")


def _build_find_command():
    """Build the ``find`` that lists git repositories while skipping system and
    cache trees (e.g. the uv git cache under ``.cache``) that hold repositories
    sendbox should never offer."""
    prune_terms = [f"-path {path}" for path in _PRUNED_PATHS]
    prune_terms += [f"-name {name}" for name in _PRUNED_DIR_NAMES]
    prune_expr = " -o ".join(prune_terms)
    return (
        f"find / \\( {prune_expr} \\) -prune "
        "-o -type d -name .git -print -prune 2>/dev/null"
    )


_FIND_GIT_REPOS = _build_find_command()


@dataclass(frozen=True)
class Identity:
    """A container user to run commands as: numeric ids and home directory."""

    uid: int
    gid: int
    home: str = ""


class IncusClient:
    """Minimal wrapper over the incus CLI exposing the calls sendbox relies on."""

    def __init__(self, binary="incus"):
        self._binary = binary
        if shutil.which(binary) is None:
            raise SendboxError(
                f"the '{binary}' command was not found on the host; is incus installed?"
            )

    def _run(self, args, input=""):
        """Invoke the incus CLI, returning stdout or raising SendboxError on failure."""
        try:
            result = subprocess.run(
                [self._binary, *args],
                input=input,
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            raise SendboxError(f"failed to execute incus: {exc}") from exc
        if result.returncode != 0:
            detail = (result.stderr or "").strip() or f"exit code {result.returncode}"
            raise SendboxError(detail)
        return result.stdout

    def state(self, container):
        """Return the runtime state of a container as a dict."""
        raw = self._run(["query", f"/1.0/instances/{container}/state"])
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SendboxError(
                f"unexpected incus response for '{container}': {exc}"
            ) from exc

    def ensure_running(self, container):
        """Validate that the container exists and is currently running."""
        status = self.state(container).get("status", "unknown")
        if status != "Running":
            raise SendboxError(
                f"container '{container}' is not running (status: {status})"
            )

    def list_containers(self):
        """Return the names of all known containers."""
        out = self._run(["list", "-c", "n", "--format", "csv"])
        return [line.strip() for line in out.splitlines() if line.strip()]

    def find_git_repositories(self, container):
        """Return absolute paths (inside the container) of every git repository."""
        out = self._run(["exec", container, "--", "sh", "-c", _FIND_GIT_REPOS])
        repos = []
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            repo = line[: -len("/.git")] if line.endswith("/.git") else line
            repos.append(repo or "/")
        return sorted(set(repos))

    def exec_command(
        self, container, command, *, identity=None, cwd=None, env=None, interactive=True
    ):
        """Build the ``incus exec`` command line that runs ``command`` in the container.

        With ``interactive`` incus picks the mode itself (a pty when the host has
        a terminal); without it plain pipes are forced.
        """
        return [
            self._binary,
            *self._exec_args(container, command, identity, cwd, env, interactive),
        ]

    def spawn(
        self,
        container,
        command,
        *,
        identity=None,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
    ):
        """Start ``command`` in the container as a background helper process.

        Helpers use plain pipes, discard incus' diagnostics and live in their own
        host session, so terminal keystrokes and signals never reach them.
        """
        argv = self.exec_command(container, command, identity=identity, interactive=False)
        try:
            return subprocess.Popen(
                argv,
                stdin=stdin,
                stdout=stdout,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as exc:
            raise SendboxError(f"failed to execute incus: {exc}") from exc

    def run_script(self, container, script, args=(), *, identity=None, input=""):
        """Run a POSIX sh ``script`` in the container with ``args`` as ``$1``..., returning stdout."""
        command = ["sh", "-c", script, "sendbox", *args]
        return self._run(
            self._exec_args(container, command, identity, None, None, False), input=input
        )

    @staticmethod
    def _exec_args(container, command, identity, cwd, env, interactive):
        """Assemble the arguments of ``incus exec`` (without the binary)."""
        args = ["exec"]
        if not interactive:
            args.append("-T")
        environment = dict(env or {})
        if identity is not None:
            args += ["--user", str(identity.uid), "--group", str(identity.gid)]
            if identity.home:
                environment.setdefault("HOME", identity.home)
        if cwd:
            args += ["--cwd", cwd]
        for key, value in environment.items():
            args += ["--env", f"{key}={value}"]
        return [*args, container, "--", *command]
