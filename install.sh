#!/usr/bin/env bash
# pichu installer — https://github.com/yeabwang/pichu
# Usage: curl -fsSL https://raw.githubusercontent.com/yeabwang/pichu/main/install.sh | bash
set -euo pipefail

REPO="yeabwang/pichu"
INSTALL_DIR="${PICHU_INSTALL_DIR:-$HOME/.local/bin}"

info()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
error() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

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

# ── Verify ──────────────────────────────────────────────────────
if command -v pichu >/dev/null 2>&1; then
    info "pichu installed successfully! 🎉"
    printf '\n'
    printf '  Run \033[1mpichu\033[0m to start.\n'
    printf '  Run \033[1mpichu /login\033[0m to configure your API key.\n'
    printf '\n'
else
    info "pichu installed but 'pichu' is not on PATH."
    printf '  Add %s to your PATH, or run:\n' "$INSTALL_DIR"
    printf '    export PATH="%s:$PATH"\n' "$INSTALL_DIR"
fi
