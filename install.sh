#!/usr/bin/env bash
# pichu installer — https://github.com/yeabwang/pichu
# Usage: curl -fsSL https://raw.githubusercontent.com/yeabwang/pichu/main/install.sh | bash
set -euo pipefail

REPO="yeabwang/pichu"
INSTALL_DIR="${PICHU_INSTALL_DIR:-$HOME/.local/bin}"
NO_MODIFY_PATH="${PICHU_NO_MODIFY_PATH:-0}"
REQUESTED_ALIAS="${PICHU_ALIAS:-pichu}"

PATH_TARGET_DIR="$INSTALL_DIR"
COMMAND_TARGET="pichu"
SHELL_KIND=""
PROFILE_FILE=""

PATH_CHANGED=0
PATH_ALREADY_PRESENT=0
PATH_SKIPPED=0
PATH_ERROR=0

ALIAS_ADDED=0
ALIAS_ALREADY_PRESENT=0
ALIAS_CONFLICT=0
ALIAS_ERROR=0

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }
error() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
    cat <<'EOF'
pichu installer

Options:
  --alias <name>      Override alias name (default: pichu)
  --no-modify-path    Do not modify shell profile PATH entries
  -h, --help          Show this help

Environment:
  PICHU_INSTALL_DIR       Override install/bin directory hint (default: ~/.local/bin)
  PICHU_ALIAS             Alias name (default: pichu)
  PICHU_NO_MODIFY_PATH=1  Skip PATH modification
EOF
}

contains_path_segment() {
    case ":$PATH:" in
        *":$1:"*) return 0 ;;
        *) return 1 ;;
    esac
}

validate_alias() {
    if [ -z "$REQUESTED_ALIAS" ]; then
        return
    fi

    if ! [[ "$REQUESTED_ALIAS" =~ ^[A-Za-z]([A-Za-z0-9_-]{0,30}[A-Za-z0-9_])?$ ]]; then
        error "Invalid alias '$REQUESTED_ALIAS'. Use 1-32 chars: letters, numbers, _ or -, starting with a letter and not ending with '-'."
    fi

    case "$REQUESTED_ALIAS" in
        alias|bg|bind|break|builtin|cd|command|continue|eval|exec|exit|export|false|fc|fg|getopts|hash|help|history|jobs|kill|local|logout|printf|pwd|read|readonly|return|set|shift|source|test|times|trap|true|type|typeset|ulimit|umask|unalias|unset|wait|if|then|elif|else|fi|for|while|until|case|esac|select|do|done|in|function|time)
            error "Alias '$REQUESTED_ALIAS' is reserved by the shell."
            ;;
    esac
}

select_profile_file() {
    if [ -n "$PROFILE_FILE" ]; then
        return 0
    fi

    local shell_name
    shell_name="$(basename "${SHELL:-sh}")"
    local xdg_config_home
    xdg_config_home="${XDG_CONFIG_HOME:-$HOME/.config}"

    case "$shell_name" in
        fish)
            SHELL_KIND="fish"
            PROFILE_FILE="$HOME/.config/fish/config.fish"
            ;;
        zsh)
            SHELL_KIND="zsh"
            PROFILE_FILE="${ZDOTDIR:-$HOME}/.zshrc"
            ;;
        bash)
            SHELL_KIND="bash"
            if [ -f "$HOME/.bashrc" ] || [ ! -f "$HOME/.profile" ]; then
                PROFILE_FILE="$HOME/.bashrc"
            elif [ -f "$HOME/.bash_profile" ]; then
                PROFILE_FILE="$HOME/.bash_profile"
            else
                PROFILE_FILE="$HOME/.profile"
            fi
            ;;
        ash|sh)
            SHELL_KIND="sh"
            PROFILE_FILE="$HOME/.profile"
            ;;
        *)
            SHELL_KIND="sh"
            if [ -f "$xdg_config_home/bash/.bashrc" ]; then
                PROFILE_FILE="$xdg_config_home/bash/.bashrc"
            else
                PROFILE_FILE="$HOME/.profile"
            fi
            ;;
    esac

    if [ -e "$PROFILE_FILE" ] && [ ! -w "$PROFILE_FILE" ]; then
        warn "Profile is not writable: $PROFILE_FILE"
        return 1
    fi

    mkdir -p "$(dirname "$PROFILE_FILE")"
    touch "$PROFILE_FILE"
}

get_path_command() {
    local escaped_path="${PATH_TARGET_DIR//\"/\\\"}"
    if [ "$SHELL_KIND" = "fish" ]; then
        printf 'fish_add_path "%s"' "$escaped_path"
    else
        printf 'export PATH="%s:$PATH"' "$escaped_path"
    fi
}

escape_single_quotes() {
    printf "%s" "$1" | sed "s/'/'\\\\''/g"
}

ensure_path_entry() {
    if contains_path_segment "$PATH_TARGET_DIR"; then
        PATH_ALREADY_PRESENT=1
        return
    fi

    if [ "$NO_MODIFY_PATH" = "1" ]; then
        PATH_SKIPPED=1
        return
    fi

    if ! select_profile_file; then
        PATH_ERROR=1
        return
    fi

    local path_cmd
    path_cmd="$(get_path_command)"

    if grep -Fqs "$path_cmd" "$PROFILE_FILE"; then
        PATH_ALREADY_PRESENT=1
        return
    fi

    {
        printf '\n'
        printf '# >>> pichu path >>>\n'
        printf '%s\n' "$path_cmd"
        printf '# <<< pichu path <<<\n'
    } >>"$PROFILE_FILE"
    PATH_CHANGED=1
}

ensure_alias_entry() {
    if [ -z "$REQUESTED_ALIAS" ]; then
        return
    fi

    if ! select_profile_file; then
        ALIAS_ERROR=1
        return
    fi

    if [ "$SHELL_KIND" = "fish" ]; then
        if grep -Fqs "# >>> pichu alias >>>" "$PROFILE_FILE" && grep -Fqs "function $REQUESTED_ALIAS" "$PROFILE_FILE"; then
            ALIAS_ALREADY_PRESENT=1
            return
        fi
        if grep -Eq "^[[:space:]]*function[[:space:]]+$REQUESTED_ALIAS([[:space:]]|\$)" "$PROFILE_FILE"; then
            ALIAS_CONFLICT=1
            return
        fi
        {
            printf '\n'
            printf '# >>> pichu alias >>>\n'
            printf 'function %s\n' "$REQUESTED_ALIAS"
            printf '    "%s" $argv\n' "$COMMAND_TARGET"
            printf 'end\n'
            printf '# <<< pichu alias <<<\n'
        } >>"$PROFILE_FILE"
        ALIAS_ADDED=1
        return
    fi

    if grep -Eq "^[[:space:]]*alias[[:space:]]+$REQUESTED_ALIAS='([^']*/)?pichu'" "$PROFILE_FILE"; then
        ALIAS_ALREADY_PRESENT=1
        return
    fi

    if grep -Eq "^[[:space:]]*alias[[:space:]]+$REQUESTED_ALIAS=" "$PROFILE_FILE"; then
        ALIAS_CONFLICT=1
        return
    fi

    local alias_target
    alias_target="$(escape_single_quotes "$COMMAND_TARGET")"
    {
        printf '\n'
        printf '# >>> pichu alias >>>\n'
        printf "alias %s='%s'\n" "$REQUESTED_ALIAS" "$alias_target"
        printf '# <<< pichu alias <<<\n'
    } >>"$PROFILE_FILE"
    ALIAS_ADDED=1
}

print_summary() {
    printf '\n'

    if [ "$PATH_CHANGED" -eq 1 ]; then
        printf 'Path environment variable modified; restart your shell to use the new value.\n'
    elif [ "$PATH_ALREADY_PRESENT" -eq 1 ]; then
        printf 'Path already contains install directory.\n'
    elif [ "$PATH_SKIPPED" -eq 1 ]; then
        printf 'Path modification skipped (--no-modify-path).\n'
    else
        printf 'Path was not modified automatically.\n'
    fi

    if [ -n "$REQUESTED_ALIAS" ]; then
        if [ "$ALIAS_ADDED" -eq 1 ]; then
            printf 'Command line alias added: "%s"\n' "$REQUESTED_ALIAS"
        elif [ "$ALIAS_ALREADY_PRESENT" -eq 1 ]; then
            printf 'Alias "%s" already configured.\n' "$REQUESTED_ALIAS"
        elif [ "$ALIAS_CONFLICT" -eq 1 ]; then
            printf 'Alias "%s" already exists with a different definition.\n' "$REQUESTED_ALIAS"
        else
            printf 'Alias "%s" was not configured automatically.\n' "$REQUESTED_ALIAS"
        fi
    fi

    if command -v pichu >/dev/null 2>&1 || [ "$PATH_CHANGED" -eq 1 ]; then
        printf 'Successfully installed. You can run "pichu" from anywhere.\n'
    else
        printf "Successfully installed, but 'pichu' is not currently on PATH in this shell.\n"
    fi

    if ! command -v pichu >/dev/null 2>&1; then
        if [ -z "$PROFILE_FILE" ]; then
            select_profile_file || true
        fi
        printf '\n'
        printf 'Manual PATH command:\n'
        if [ "$SHELL_KIND" = "fish" ]; then
            printf '  fish_add_path "%s"\n' "$PATH_TARGET_DIR"
        else
            printf '  export PATH="%s:$PATH"\n' "$PATH_TARGET_DIR"
        fi
        if [ -n "$PROFILE_FILE" ]; then
            printf 'Profile file: %s\n' "$PROFILE_FILE"
        fi
    fi
}

parse_args() {
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --alias)
                shift
                [ "$#" -gt 0 ] || error "Missing value for --alias"
                REQUESTED_ALIAS="$1"
                ;;
            --no-modify-path)
                NO_MODIFY_PATH=1
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            *)
                error "Unknown option: $1"
                ;;
        esac
        shift
    done
}

parse_args "$@"
validate_alias

# ── Check dependencies ──────────────────────────────────────────
command -v python3 >/dev/null 2>&1 || error "Python 3.11+ is required. Install it first."

PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)

if [ "$PYTHON_MAJOR" -lt 3 ] || { [ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 11 ]; }; then
    error "Python 3.11+ is required (found $PYTHON_VERSION)."
fi

# ── Prefer uv, fall back to pip ─────────────────────────────────
if command -v uv >/dev/null 2>&1; then
    INSTALLER="uv"
elif command -v pipx >/dev/null 2>&1; then
    INSTALLER="pipx"
elif command -v pip3 >/dev/null 2>&1; then
    INSTALLER="pip3"
elif command -v pip >/dev/null 2>&1; then
    INSTALLER="pip"
else
    error "No package installer found. Install uv (recommended): curl -LsSf https://astral.sh/uv/install.sh | sh"
fi

info "Installing pichu with $INSTALLER..."

case "$INSTALLER" in
    uv)
        uv tool install "pichu @ git+https://github.com/${REPO}.git"
        ;;
    pipx)
        pipx install "git+https://github.com/${REPO}.git"
        ;;
    pip3|pip)
        "$INSTALLER" install --user "git+https://github.com/${REPO}.git"
        ;;
esac

if command -v pichu >/dev/null 2>&1; then
    COMMAND_TARGET="$(command -v pichu)"
    PATH_TARGET_DIR="$(dirname "$COMMAND_TARGET")"
else
    COMMAND_TARGET="$PATH_TARGET_DIR/pichu"
fi

ensure_path_entry
ensure_alias_entry
print_summary

if [ "$PATH_ERROR" -eq 1 ] || [ "$ALIAS_ERROR" -eq 1 ]; then
    exit 1
fi
