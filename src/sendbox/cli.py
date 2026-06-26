"""Command line interface: argument parsing and orchestration."""

import os
import shutil
import sys

from . import __version__
from .completion import print_completion
from .errors import SendboxError
from .git import run_git
from .incus import IncusClient
from .mounting import IncusFileMount

_USAGE = "sendbox [--repo PATH] <container> <git command> [args...]"

_HELP = f"""sendbox — run host-side git commands against repositories inside incus containers.

Usage:
  {_USAGE}
  sendbox completion <bash|zsh>
  sendbox --help | --version

Description:
  sendbox lets you run git on the host against a repository that lives inside an
  incus container. The repository is briefly mounted onto the host with
  `incus file mount`, the git command runs against it using your credentials,
  and the mount is ALWAYS removed afterwards — even if the command fails or is
  interrupted.

  This is useful when whatever runs inside the container must not hold git remote
  credentials: you keep the keys on the host and push/pull from there.

Arguments:
  <container>          Name of the incus container (must be running).
  <git command>        Any git invocation, e.g. `push origin main`.

Options:
  -r, --repo PATH      Select a repository by absolute path or by name when the
                       container holds more than one. Without it, the single
                       repository found is used; if several exist you must choose.
  -h, --help           Show this help and exit.
  -V, --version        Show the version and exit.

Examples:
  sendbox agent-1 status
  sendbox agent-1 push origin main
  sendbox --repo backend agent-1 pull --rebase

Notes:
  * Uses `incus file mount`, which needs sshfs on the host (no root required).
  * git runs as you, so your ssh keys and git config are used for authentication.
  * Shell completion: `sendbox completion bash` / `sendbox completion zsh`.
"""


def main(argv=None):
    """Entry point for the ``sendbox`` console command."""
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        return _dispatch(argv)
    except SendboxError as exc:
        print(f"sendbox: error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


def _dispatch(argv):
    """Route the parsed arguments to the right action."""
    if not argv or argv[0] in ("-h", "--help"):
        print(_HELP)
        return 0
    if argv[0] in ("-V", "--version"):
        print(f"sendbox {__version__}")
        return 0
    if argv[0] == "completion":
        return _completion(argv[1:])

    repo_opt, rest = _extract_repo_option(argv)
    if not rest:
        raise SendboxError(f"missing container name\nusage: {_USAGE}")
    container, git_args = rest[0], rest[1:]
    if not git_args:
        raise SendboxError(f"missing git command\nusage: {_USAGE}")

    return _run(container, git_args, repo_opt)


def _extract_repo_option(argv):
    """Pull a leading --repo/-r option (and its value) out of the argument list."""
    repo = None
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("-r", "--repo"):
            if i + 1 >= len(argv):
                raise SendboxError(f"option {arg} requires a value")
            repo = argv[i + 1]
            i += 2
            continue
        if arg.startswith("--repo="):
            repo = arg.split("=", 1)[1]
            i += 1
            continue
        break
    return repo, argv[i:]


def _require_sshfs():
    """Abort early with a clear message if the host lacks sshfs."""
    if shutil.which("sshfs") is None:
        raise SendboxError(
            "`incus file mount` needs sshfs on the host, but it was not found; "
            "please install sshfs"
        )


def _run(container, git_args, repo_opt):
    """Mount the selected repository, run git against it, and unmount."""
    _require_sshfs()
    client = IncusClient()
    client.ensure_running(container)
    repo = _resolve_repository(client, container, repo_opt)
    source = f"{container}{repo}"
    with IncusFileMount(source) as mountpoint:
        return run_git(mountpoint, git_args)


def _resolve_repository(client, container, repo_opt):
    """Pick exactly one repository inside the container, or fail with guidance."""
    repos = client.find_git_repositories(container)
    if not repos:
        raise SendboxError(
            f"no git repository was found inside container '{container}'"
        )
    if repo_opt:
        matches = [r for r in repos if r == repo_opt or os.path.basename(r) == repo_opt]
        if not matches:
            listing = "\n  ".join(repos)
            raise SendboxError(
                f"no repository matching '{repo_opt}' in '{container}'; found:\n  {listing}"
            )
        if len(matches) > 1:
            listing = "\n  ".join(matches)
            raise SendboxError(f"'{repo_opt}' is ambiguous; matches:\n  {listing}")
        return matches[0]
    if len(repos) > 1:
        listing = "\n  ".join(repos)
        raise SendboxError(
            "multiple git repositories found; choose one with --repo:\n  " + listing
        )
    return repos[0]


def _completion(args):
    """Print the requested shell completion script."""
    if not args:
        raise SendboxError("usage: sendbox completion <bash|zsh>")
    print_completion(args[0])
    return 0
