#!/usr/bin/env bash
# pichu installer — https://github.com/yeabwang/pichu
set -euo pipefail
set -o noclobber
umask 077
export LC_ALL=C

# Validate HOME before any path computations
case "${HOME:-}" in
    /*) ;;
    *)  printf '\033[1;31merror:\033[0m HOME must be set to an absolute path.\n' >&2; exit 1 ;;
esac

# ── Constants ────────────────────────────────────────────────────────────────
readonly REPO="yeabwang/pichu"
readonly PICHU_VERSION="${PICHU_VERSION:-main}"
case "$PICHU_VERSION" in
    *[!A-Za-z0-9._/-]*) printf '\033[1;31merror:\033[0m PICHU_VERSION contains invalid characters.\n' >&2; exit 1 ;;
esac
readonly INSTALL_DIR="${PICHU_INSTALL_DIR:-$HOME/.local/bin}"
case "$INSTALL_DIR" in
    /*) ;;
    *)  printf '\033[1;31merror:\033[0m PICHU_INSTALL_DIR must be an absolute path.\n' >&2; exit 1 ;;
esac
NO_MODIFY_PATH="${PICHU_NO_MODIFY_PATH:-0}"
REQUESTED_ALIAS="${PICHU_ALIAS:-pichu}"   # may be overridden by --alias flag

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

_PICHU_TMPFILES=()
_cleanup() {
    local _f
    for _f in "${_PICHU_TMPFILES[@]+"${_PICHU_TMPFILES[@]}"}"; do
        rm -f "$_f" 2>/dev/null || true
    done
}
trap _cleanup EXIT

# ── Helpers ──────────────────────────────────────────────────────────────────
info()  { printf '\033[1;34m==>\033[0m %s\n'         "$*"; }
warn()  { printf '\033[1;33mwarning:\033[0m %s\n'    "$*" >&2; }
error() { printf '\033[1;31merror:\033[0m %s\n'      "$*" >&2; exit 1; }

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
  PICHU_VERSION           Git ref to install (default: main)

Security note:
  Download to a file first rather than piping to bash:
    curl --proto '=https' --tlsv1.2 -fsSL <url> -o install.sh && bash install.sh
EOF
}

# ── Alias validation ─────────────────────────────────────────────────────────
validate_alias() {
    [ -z "$REQUESTED_ALIAS" ] && return 0

    case "$REQUESTED_ALIAS" in
        -*) error "Invalid alias '$REQUESTED_ALIAS': must not start with '-'." ;;
    esac

    if ! printf '%s' "$REQUESTED_ALIAS" | \
         grep -Eq '^[A-Za-z][A-Za-z0-9_-]{0,30}[A-Za-z0-9_]$|^[A-Za-z]$'; then
        error "Invalid alias '$REQUESTED_ALIAS'. Use 1–32 chars: letters, numbers, _ or -, starting with a letter, not ending with '-'."
    fi

    case "$REQUESTED_ALIAS" in
        alias|bg|bind|break|builtin|cd|command|continue|eval|exec|exit|\
        export|false|fc|fg|getopts|hash|help|history|jobs|kill|local|\
        logout|printf|pwd|read|readonly|return|set|shift|source|test|\
        times|trap|true|type|typeset|ulimit|umask|unalias|unset|wait|\
        if|then|elif|else|fi|for|while|until|case|esac|select|do|done|\
        in|function|time)
            error "Alias '$REQUESTED_ALIAS' is a reserved shell word."
            ;;
    esac
}

regex_escape() {
    printf '%s' "$1" | sed 's/[][\\.*^$+?{}()|-]/\\&/g'
}

# ── Profile-file selection ───────────────────────────────────────────────────
select_profile_file() {
    [ -n "$PROFILE_FILE" ] && return 0

    local shell_name xdg_config_home candidate
    shell_name="$(basename "${SHELL:-sh}")"
    xdg_config_home="${XDG_CONFIG_HOME:-$HOME/.config}"

    case "$shell_name" in
        fish) SHELL_KIND="fish"; PROFILE_FILE="$HOME/.config/fish/config.fish" ;;
        zsh)  SHELL_KIND="zsh";  PROFILE_FILE="${ZDOTDIR:-$HOME}/.zshrc" ;;
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
        ash|sh) SHELL_KIND="sh"; PROFILE_FILE="$HOME/.profile" ;;
        *)
            SHELL_KIND="sh"
            candidate="$xdg_config_home/bash/.bashrc"
            PROFILE_FILE="$([ -f "$candidate" ] && echo "$candidate" || echo "$HOME/.profile")"
            ;;
    esac

    # ── Security: verify profile file is safe to write ───────────────────────
    local profile_dir
    profile_dir="$(dirname "$PROFILE_FILE")"
    if [ ! -d "$profile_dir" ]; then
        mkdir -p -m 0700 "$profile_dir" || { warn "Cannot create $profile_dir"; return 1; }
    fi

    # Refuse symlinks
    if [ -L "$PROFILE_FILE" ]; then
        warn "Profile path '$PROFILE_FILE' is a symlink — refusing to write to it."
        return 1
    fi

    if [ ! -e "$PROFILE_FILE" ]; then
        (set -o noclobber; > "$PROFILE_FILE") 2>/dev/null || {
            warn "Cannot create profile '$PROFILE_FILE'"; return 1;
        }
        chmod 0600 "$PROFILE_FILE"
    fi

    if [ ! -f "$PROFILE_FILE" ]; then
        warn "Profile '$PROFILE_FILE' is not a regular file."
        return 1
    fi

    if [ ! -w "$PROFILE_FILE" ]; then
        warn "Profile '$PROFILE_FILE' is not writable."
        return 1
    fi
}

append_to_profile() {
    if [ -L "$PROFILE_FILE" ]; then
        warn "Refusing to write: '$PROFILE_FILE' is a symlink."
        return 1
    fi
    cat "$1" >> "$PROFILE_FILE"
}

# ── PATH helpers ─────────────────────────────────────────────────────────────
contains_path_segment() {
    case ":$PATH:" in *":$1:"*) return 0 ;; esac
    return 1
}

get_path_command() {
    local safe_dir
    safe_dir="$(printf '%s' "$PATH_TARGET_DIR" | sed "s/'/'\\''/g")"
    if [ "$SHELL_KIND" = "fish" ]; then
        printf "fish_add_path '%s'" "$safe_dir"
    else
        printf "export PATH='%s:\$PATH'" "$safe_dir"
    fi
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

    local tmp
    tmp="$(mktemp "${PROFILE_FILE}.pichu.XXXXXXXX")"
    _PICHU_TMPFILES+=("$tmp")
    {
        printf '\n'
        printf '# >>> pichu path >>>\n'
        printf '%s\n' "$path_cmd"
        printf '# <<< pichu path <<<\n'
    } > "$tmp"
    if ! append_to_profile "$tmp"; then PATH_ERROR=1; rm -f "$tmp"; return; fi
    rm -f "$tmp"
    PATH_CHANGED=1
}

# ── Alias helpers ─────────────────────────────────────────────────────────────
ensure_alias_entry() {
    [ -z "$REQUESTED_ALIAS" ] && return

    if ! select_profile_file; then
        ALIAS_ERROR=1
        return
    fi

    local escaped_alias
    escaped_alias="$(regex_escape "$REQUESTED_ALIAS")"

    if [ "$SHELL_KIND" = "fish" ]; then
        if grep -Fqs "# >>> pichu alias >>>" "$PROFILE_FILE" && \
           grep -Eq "^[[:space:]]*function[[:space:]]+${escaped_alias}([[:space:]]|\$)" "$PROFILE_FILE"; then
            ALIAS_ALREADY_PRESENT=1
            return
        fi
        if grep -Eq "^[[:space:]]*function[[:space:]]+${escaped_alias}([[:space:]]|\$)" "$PROFILE_FILE"; then
            ALIAS_CONFLICT=1
            return
        fi

        local tmp
        tmp="$(mktemp "${PROFILE_FILE}.pichu.XXXXXXXX")"
        _PICHU_TMPFILES+=("$tmp")
        {
            printf '\n'
            printf '# >>> pichu alias >>>\n'
            printf 'function %s\n' "$REQUESTED_ALIAS"
            printf "    '%s' \$argv\n" "$COMMAND_TARGET"
            printf 'end\n'
            printf '# <<< pichu alias <<<\n'
        } > "$tmp"
        if ! append_to_profile "$tmp"; then ALIAS_ERROR=1; rm -f "$tmp"; return; fi
        rm -f "$tmp"
        ALIAS_ADDED=1
        return
    fi

    if grep -Eq "^[[:space:]]*alias[[:space:]]+${escaped_alias}='([^']*/)?pichu'" "$PROFILE_FILE"; then
        ALIAS_ALREADY_PRESENT=1
        return
    fi

    # Conflict: alias name defined with a different value
    if grep -Eq "^[[:space:]]*alias[[:space:]]+${escaped_alias}=" "$PROFILE_FILE"; then
        ALIAS_CONFLICT=1
        return
    fi

    local safe_target
    safe_target="$(printf '%s' "$COMMAND_TARGET" | sed "s/'/'\\''/g")"

    local tmp
    tmp="$(mktemp "${PROFILE_FILE}.pichu.XXXXXXXX")"
    _PICHU_TMPFILES+=("$tmp")
    {
        printf '\n'
        printf '# >>> pichu alias >>>\n'
        printf "alias %s='%s'\n" "$REQUESTED_ALIAS" "$safe_target"
        printf '# <<< pichu alias <<<\n'
    } > "$tmp"
    if ! append_to_profile "$tmp"; then ALIAS_ERROR=1; rm -f "$tmp"; return; fi
    rm -f "$tmp"
    ALIAS_ADDED=1
}

# ── Summary ──────────────────────────────────────────────────────────────────
print_summary() {
    printf '\n'

    if   [ "$PATH_CHANGED"         -eq 1 ]; then printf 'PATH modified in %s — restart your shell.\n' "$PROFILE_FILE"
    elif [ "$PATH_ALREADY_PRESENT" -eq 1 ]; then printf 'PATH already contains install directory.\n'
    elif [ "$PATH_SKIPPED"         -eq 1 ]; then printf 'PATH modification skipped (--no-modify-path).\n'
    else                                         printf 'PATH was not modified automatically.\n'
    fi

    if [ -n "$REQUESTED_ALIAS" ]; then
        if   [ "$ALIAS_ADDED"          -eq 1 ]; then printf 'Alias "%s" added to %s.\n' "$REQUESTED_ALIAS" "$PROFILE_FILE"
        elif [ "$ALIAS_ALREADY_PRESENT" -eq 1 ]; then printf 'Alias "%s" already configured.\n' "$REQUESTED_ALIAS"
        elif [ "$ALIAS_CONFLICT"       -eq 1 ]; then printf 'Alias "%s" already exists with a different definition — not modified.\n' "$REQUESTED_ALIAS"
        else                                         printf 'Alias "%s" was not configured automatically.\n' "$REQUESTED_ALIAS"
        fi
    fi

    if command -v pichu >/dev/null 2>&1 || [ "$PATH_CHANGED" -eq 1 ]; then
        printf 'Successfully installed. Run "pichu" from anywhere.\n'
    else
        printf "Installed, but 'pichu' is not yet on PATH in this shell.\n"
        if [ "$SHELL_KIND" = "fish" ]; then
            printf '  Manual: fish_add_path "%s"\n' "$PATH_TARGET_DIR"
        else
            printf '  Manual: export PATH="%s:$PATH"\n' "$PATH_TARGET_DIR"
        fi
        [ -n "$PROFILE_FILE" ] && printf '  Profile: %s\n' "$PROFILE_FILE"
    fi
}

# ── Argument parsing ─────────────────────────────────────────────────────────
parse_args() {
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --alias)
                shift
                [ "$#" -gt 0 ] || error "Missing value for --alias."
                REQUESTED_ALIAS="$1"
                ;;
            --no-modify-path) NO_MODIFY_PATH=1 ;;
            -h|--help) usage; exit 0 ;;
            # Reject any unknown flag to avoid silent misuse
            -*) error "Unknown option: $1" ;;
            *)  error "Unexpected argument: $1" ;;
        esac
        shift
    done
}

# ════════════════════════════════════════════════════════════════════════════
parse_args "$@"
validate_alias

# ── Python version check ─────────────────────────────────────────────────────
command -v python3 >/dev/null 2>&1 || error "Python 3.11+ is required. Install it first."

PYTHON_VERSION="$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")"
if ! printf '%s' "$PYTHON_VERSION" | grep -Eq '^[0-9]+\.[0-9]+$'; then
    error "Unexpected Python version output: '$PYTHON_VERSION'"
fi
PYTHON_MAJOR="${PYTHON_VERSION%%.*}"
PYTHON_MINOR="${PYTHON_VERSION##*.}"

{ [ "$PYTHON_MAJOR" -gt 3 ] || { [ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -ge 11 ]; }; } || \
    error "Python 3.11+ required (found $PYTHON_VERSION)."

# ── Select installer ─────────────────────────────────────────────────────────
if   command -v uv    >/dev/null 2>&1; then INSTALLER="uv"
elif command -v pipx  >/dev/null 2>&1; then INSTALLER="pipx"
elif command -v pip3  >/dev/null 2>&1; then INSTALLER="pip3"
elif command -v pip   >/dev/null 2>&1; then INSTALLER="pip"
else error "No package installer found. Install uv: https://docs.astral.sh/uv/getting-started/installation/"
fi

info "Installing pichu with $INSTALLER…"

# ── Install ──────────────────────────────────────────────────────────────────
case "$INSTALLER" in
    uv)         uv tool install --force "pichu @ git+https://github.com/${REPO}.git@${PICHU_VERSION}" ;;
    pipx)       pipx install "git+https://github.com/${REPO}.git@${PICHU_VERSION}" ;;
    pip3|pip)   "$INSTALLER" install --user "git+https://github.com/${REPO}.git@${PICHU_VERSION}" ;;
esac

# ── Locate installed binary ───────────────────────────────────────────────────
if [ -x "$INSTALL_DIR/pichu" ]; then
    COMMAND_TARGET="$INSTALL_DIR/pichu"
    PATH_TARGET_DIR="$INSTALL_DIR"
elif command -v pichu >/dev/null 2>&1; then
    COMMAND_TARGET="$(command -v pichu)"
    PATH_TARGET_DIR="$(dirname "$COMMAND_TARGET")"
else
    COMMAND_TARGET="$PATH_TARGET_DIR/pichu"
fi

ensure_path_entry
ensure_alias_entry
print_summary

if [ "$PATH_ERROR" -eq 1 ] || [ "$ALIAS_ERROR" -eq 1 ]; then exit 1; fi
