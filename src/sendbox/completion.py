"""Shell completion scripts for bash and zsh.

Both scripts complete the incus container name in the first position (via
``incus list``) and a curated list of git subcommands in the second, falling
back to file completion afterwards.
"""

import sys

from .errors import SendboxError

_GIT_COMMANDS = (
    "add am annotate apply archive bisect blame branch bundle checkout cherry-pick "
    "clean clone commit config describe diff fetch format-patch gc grep init log "
    "ls-files merge mv notes pull push range-diff rebase reflog remote reset restore "
    "revert rm shortlog show stash status submodule switch tag whatchanged worktree"
)

_BASH = r"""# bash completion for sendbox
_sendbox() {
    local cur prev
    COMPREPLY=()
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"

    if [[ "$prev" == "--repo" || "$prev" == "-r" ]]; then
        return 0
    fi

    local git_commands="__GIT_COMMANDS__"

    # Locate the container argument, skipping leading options and their values.
    local container_index=-1 idx=1
    while [[ $idx -lt $COMP_CWORD ]]; do
        case "${COMP_WORDS[idx]}" in
            -r|--repo) idx=$((idx + 2)); continue ;;
            -*) idx=$((idx + 1)); continue ;;
            *) container_index=$idx; break ;;
        esac
    done

    if [[ $container_index -eq -1 ]]; then
        if [[ "$cur" == -* ]]; then
            COMPREPLY=( $(compgen -W "--help --version --repo" -- "$cur") )
        else
            local containers
            containers=$(incus list -c n --format csv 2>/dev/null)
            COMPREPLY=( $(compgen -W "completion $containers" -- "$cur") )
        fi
        return 0
    fi

    if [[ "${COMP_WORDS[container_index]}" == "completion" ]]; then
        COMPREPLY=( $(compgen -W "bash zsh" -- "$cur") )
        return 0
    fi

    if [[ $COMP_CWORD -eq $((container_index + 1)) ]]; then
        COMPREPLY=( $(compgen -W "$git_commands" -- "$cur") )
        return 0
    fi

    COMPREPLY=( $(compgen -f -- "$cur") )
    return 0
}
complete -F _sendbox sendbox
"""

_ZSH = r"""#compdef sendbox
# zsh completion for sendbox
_sendbox() {
    local -a git_commands
    git_commands=(__GIT_COMMANDS__)

    if (( CURRENT == 2 )); then
        local -a containers
        containers=(${(f)"$(incus list -c n --format csv 2>/dev/null)"})
        _alternative \
            'commands:command:(completion)' \
            'containers:container:compadd -a containers' \
            'options:option:(--help --version --repo)'
        return
    fi

    if [[ ${words[2]} == completion ]]; then
        (( CURRENT == 3 )) && compadd bash zsh
        return
    fi

    if (( CURRENT == 3 )); then
        compadd -a git_commands
        return
    fi

    _files
}

_sendbox "$@"
"""


def print_completion(shell):
    """Print the shell completion script for the requested shell to stdout."""
    if shell == "bash":
        script = _BASH
    elif shell == "zsh":
        script = _ZSH
    else:
        raise SendboxError("usage: sendbox completion <bash|zsh>")
    sys.stdout.write(script.replace("__GIT_COMMANDS__", _GIT_COMMANDS))
