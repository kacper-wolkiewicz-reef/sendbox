# sendbox

Run **git inside incus containers** while the **ssh authentication happens on the
host** — without ever giving the container your git remote credentials.

`sendbox` finds a git repository inside a container and runs your git command
**there**, as the repository's owner. Whenever that git needs an ssh connection
(`push`, `pull`, `fetch`, ...), the connection is made by **ssh on the host**, with
your keys, agent and `~/.ssh/config`; only the git protocol bytes travel back into
the container. Nothing is mounted, and the container never sees a key or an agent
socket.

## Why

You run untrusted agents inside incus containers (full root, making changes to
checked-out repositories). You do **not** want those containers to hold push/pull
credentials, so the host must do the authenticating. `sendbox` is the bridge:
keys stay on the host, the repo — and git itself — stay in the container.

## How it works

1. Checks the container is running (via `incus query`).
2. Runs `find` **inside the container** (via `incus exec`) to locate git repositories.
3. Creates a private session directory inside the container, owned by the
   repository's owner. It holds a tiny POSIX-sh **ssh shim** and a FIFO on which
   the shim asks the host for connections; a helper `incus exec` listens on it.
4. Runs `git <your command>` in the repository with `incus exec`, as the owner of
   the repository, with `GIT_SSH_COMMAND` pointing at the shim.
5. When git runs "ssh", the shim hands its arguments to the host through a
   per-connection pair of FIFOs. sendbox **validates** them (they must look
   exactly like what git passes to ssh, for `git-upload-pack`,
   `git-receive-pack` or `git-upload-archive`), rebuilds a hardened ssh command
   line from scratch and runs it on the host, pumping bytes between it and the
   shim over two helper `incus exec` streams. ssh's exit code goes back to git.
6. Stops every helper and removes the session directory — **guaranteed**.

The "always clean up" guarantee is enforced by three independent layers: a
context manager, an `atexit` hook, and `SIGINT`/`SIGTERM`/`SIGHUP` handlers.
The container's configuration is never modified (no devices, no proxies).

Because git runs inside the container as the repository's owner, files are
created with the **correct ownership**, and the repository is never touched
through a network filesystem.

## Requirements

- `incus` on the host, with the invoking user able to talk to it.
- `ssh` (OpenSSH client) on the host.
- `git` and a POSIX shell with `mkfifo`, `mktemp`, `stat` and `cat` inside the
  container (coreutils or busybox).
- A **running** target container.
- (Optional) `bash-completion` for the bash completion script.

No root or `sudo` is required on the host.

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

If the container holds **more than one** repository, `sendbox` prompts you to pick
one interactively (arrow keys + Enter). When there is no terminal to prompt on
(e.g. output is piped), it lists the repositories and asks you to choose with
`--repo` (an absolute path, or just the repo's directory name).

Run `sendbox --help` for the full help.

## Credentials

ssh runs on the host as the user who invoked `sendbox`, so it uses your
`~/.ssh/config` (host aliases work), `known_hosts`, keys and `ssh-agent` — exactly
as a normal `git push` would. Keep your deploy/push keys on the host as usual.

Two differences from a plain `git push`, because the terminal belongs to git
inside the container while it runs:

- ssh runs with `BatchMode=yes`: it cannot ask for a passphrase or confirm an
  unknown host key. Load passphrase-protected keys into `ssh-agent`, and connect
  to a new server once by hand (e.g. `ssh -T git@github.com`) to record its key.
- Only **ssh** remotes get host credentials. `https` remotes are fetched and
  pushed from inside the container with whatever credentials it has (usually
  none).

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

## Security model

- The container never gets a key, an agent socket or a forwarded port. Each
  connection it asks for is announced on your terminal
  (`sendbox: ssh git@github.com git-receive-pack 'org/repo.git'`).
- Everything the container sends is treated as hostile: anything but the exact
  argument shape git produces is refused, and the host ssh command line is
  rebuilt from the validated values, with `BatchMode=yes`, `ForwardAgent=no`,
  `ForwardX11=no`, `ClearAllForwardings=yes`, `PermitLocalCommand=no`,
  `GSSAPIDelegateCredentials=no`, `Tunnel=no` and `RequestTTY=no`.
- The relay exists only while your git command runs, and serves at most 16
  connections at once.
- Within that window, a process inside the container **can** use the relay for
  git transport commands against any server and repository your ssh identity
  reaches — just as it controls which remote your `sendbox ... push` goes to.
  Scope your keys accordingly (e.g. per-repository deploy keys).
- Only a `SIGKILL` of `sendbox` itself can leave the session directory behind
  (under `/tmp/sendbox.*` in the container); it holds no secrets.

## License

MIT.
