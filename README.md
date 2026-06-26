# sendbox

Run **host-side git commands** against repositories that live **inside incus
containers** — without ever giving the container your git remote credentials.

`sendbox` finds a git repository inside a container, **briefly mounts it onto the
host with `incus file mount`**, runs your git command against it using your own
credentials, and then **always removes the mount** — on success, on failure, on
`Ctrl-C`, on `SIGTERM`. The mount is never left behind.

## Why

You run untrusted agents inside incus containers (full root, making changes to
checked-out repositories). You do **not** want those containers to hold push/pull
credentials, so the host must do the pushing and pulling. `sendbox` is the bridge:
keys stay on the host, the repo stays in the container.

## How it works

1. Checks the container is running (via `incus query`).
2. Runs `find` **inside the container** (via `incus exec`) to locate git repositories.
3. Mounts the repository onto a temporary mountpoint with `incus file mount`
   (an sshfs mount served by incus). This command is foreground and blocking, so
   sendbox runs it as a background process and waits until the mount is live.
4. Runs `git -C <mountpoint> <your command>` with your identity.
5. Signals the mount process to unmount cleanly, with a `fusermount`/`umount`
   fallback, and removes the temporary mountpoint — **guaranteed**.

The "always unmount" guarantee is enforced by three independent layers: a
`try/finally`, an `atexit` hook, and `SIGINT`/`SIGTERM`/`SIGHUP` handlers. If the
mount process does not clean up by itself, sendbox falls back to
`fusermount -u`, then a lazy `fusermount -uz` / `umount -l`.

Because writes travel through incus' SFTP server (which acts as the container's
root), files are created with the **correct in-container ownership** — there is no
host-vs-container UID mismatch to clean up afterwards.

## Requirements

- `incus` on the host, with the invoking user able to talk to it.
- `sshfs` on the host (used by `incus file mount`).
- `git` on the host.
- A **running** target container.
- (Optional) `bash-completion` for the bash completion script.

No root or `sudo` is required: `incus file mount` uses FUSE, and git runs as you.

## Installation

From the project directory:

```bash
# Recommended: isolated install via pipx
pipx install .

# Or with uv
uv tool install .

# Or a plain user install
pip install --user .
```

This installs the `sendbox` command onto your `PATH`.

## Usage

```text
sendbox [--repo PATH] <container> <git command> [args...]
```

Examples:

```bash
sendbox agent-1 status
sendbox agent-1 push origin main
sendbox agent-1 pull --rebase
sendbox --repo backend agent-1 log --oneline -n 5
```

If the container holds **more than one** repository, `sendbox` lists them and asks
you to pick one with `--repo` (an absolute path, or just the repo's directory name).

Run `sendbox --help` for the full help.

## Credentials

git runs as the user who invoked `sendbox`, so it reads your `~/.gitconfig` and ssh
finds your `~/.ssh` keys — exactly as a normal `git push` would. Keep your
deploy/push keys on the host as usual.

## Shell completion

Completion covers the container name (from `incus list`) and common git subcommands.

**bash:**

```bash
# one-off, current shell
source <(sendbox completion bash)

# persistent (system-wide)
sendbox completion bash | sudo tee /etc/bash_completion.d/sendbox >/dev/null
```

**zsh:**

```bash
# put it on your fpath, e.g.
sendbox completion zsh > ~/.zfunc/_sendbox
# ensure ~/.zfunc is on fpath and compinit runs in ~/.zshrc:
#   fpath=(~/.zfunc $fpath)
#   autoload -Uz compinit && compinit
```

## Safety

- The mount lives only for the duration of a single git command.
- The mount is **always** torn down — there is no flag to keep it.
- The temporary mountpoint is created under the system temp dir and removed after use.

## License

MIT.
