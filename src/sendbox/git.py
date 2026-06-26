"""Running the user's git command against the mounted repository."""

import subprocess


def run_git(workdir, git_args):
    """Run git inside ``workdir``, returning git's exit code.

    The repository is reached over sshfs, so its files appear with the
    container's ownership; ``safe.directory=*`` keeps git from refusing to
    operate on a tree it considers owned by someone else.
    """
    command = ["git", "-C", workdir, "-c", "safe.directory=*", *git_args]
    return subprocess.run(command).returncode
