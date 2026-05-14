#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# Nanobot-Lite Bootstrap Installer  (curl | bash)
# Works on: Linux, macOS, Termux (Android 32/64-bit)
# No pip required for bootstrap — curl only.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${BLUE}[INFO]${RESET} $1"; }
success() { echo -e "${GREEN}[OK]${RESET}   $1"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET} $1"; }
error()   { echo -e "${RED}[ERR]${RESET}  $1"; }
section() { echo -e "\n${CYAN}${BOLD}══ $1 ══${RESET}"; }

# ── Detect platform ──────────────────────────────────────────────────────────
detect_platform() {
    if [ -f /proc/version ] && grep -qi android /proc/version; then
        echo "termux"
    elif [ "$(uname)" = "Darwin" ]; then
        echo "macos"
    elif [ "$(uname)" = "Linux" ]; then
        echo "linux"
    else
        echo "unknown"
    fi
}

PLATFORM=$(detect_platform)
info "Platform detected: ${BOLD}${PLATFORM}${RESET}"

# ── Helpers ───────────────────────────────────────────────────────────────────
need_cmd() {
    if ! command -v "$1" &>/dev/null; then
        error "Required command not found: $1"
        if [ "$PLATFORM" = "termux" ]; then
            info "Run: pkg install $1"
        fi
        exit 1
    fi
}

latest_tag() {
    curl -s --max-time 10 "https://api.github.com/repos/tundefund0-gif/nanobot-lite/releases/latest" \
        | grep '"tag_name":' | sed -E 's/.*"([^"]+)".*/\1/'
}

latest_commit() {
    curl -s --max-time 10 \
        "https://api.github.com/repos/tundefund0-gif/nanobot-lite/commits/main" \
        | python3 -c "import sys,json; print(json.load(sys.stdin)['sha'][:8])" 2>/dev/null || echo "main"
}

# ── Bootstrap: install Python / pip if missing ─────────────────────────────────
bootstrap_python() {
    section "Python Setup"

    if command -v python3 &>/dev/null; then
        PYTHON=$(command -v python3)
        PYVER=$(python3 --version 2>&1 | cut -d' ' -f2)
        success "Python already installed: ${PYTHON} (${PYVER})"
    else
        if [ "$PLATFORM" = "termux" ]; then
            info "Installing Python via pkg..."
            pkg install -y python
        elif [ "$PLATFORM" = "macos" ]; then
            info "Installing Python via brew..."
            need_cmd brew
            brew install python3
        else
            info "Installing Python via apt..."
            need_cmd apt-get
            apt-get update && apt-get install -y python3 python3-pip python3-venv
        fi
        PYTHON=$(command -v python3)
        PYVER=$(python3 --version 2>&1 | cut -d' ' -f2)
        success "Python installed: ${PYTHON} (${PYVER})"
    fi

    # Ensure pip
    if ! command -v pip3 &>/dev/null && ! $PYTHON -m pip --version &>/dev/null; then
        info "Installing pip..."
        if [ "$PLATFORM" = "termux" ]; then
            pkg install -y python-pip
        else
            curl -sS https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py
            $PYTHON /tmp/get-pip.py --quiet
            rm -f /tmp/get-pip.py
        fi
        success "pip installed"
    fi

    # Upgrade pip + setuptools + wheel
    info "Upgrading pip..."
    $PYTHON -m pip install --quiet --upgrade pip setuptools wheel
}

# ── Create / upgrade venv ─────────────────────────────────────────────────────
setup_venv() {
    section "Virtual Environment"

    VENV_DIR="${HOME}/nanobot_env"
    OVERWRITE=0

    if [ -d "$VENV_DIR" ]; then
        read -p "nanobot_env already exists. Recreate it? [y/N]: " ans
        case "$ans" in [yY]*) OVERWRITE=1; rm -rf "$VENV_DIR";; esac
    fi

    if [ $OVERWRITE -eq 1 ] || [ ! -d "$VENV_DIR" ]; then
        info "Creating virtual environment at ${VENV_DIR}..."
        $PYTHON -m venv --without-pip "$VENV_DIR"
        success "venv created"
    else
        success "Reusing existing venv"
    fi

    # Use python -m pip directly (no separate pip binary needed inside venv)
    # Works on Termux since system pip is available; ensurepip fallback
    PIP="$VENV_DIR/bin/pip"
    if [ ! -f "$PIP" ]; then
        info "Setting up pip for venv..."
        "$VENV_DIR/bin/python" -m ensurepip --upgrade 2>/dev/null || {
            info "ensurepip unavailable — system pip will work directly"
        }
    fi

    # Upgrade pip in venv
    "$NANOBOT_PYTHON" -m pip install --quiet --upgrade pip setuptools wheel
    success "venv pip ready"

    export VENV_DIR
    export NANOBOT_PYTHON="$VENV_DIR/bin/python"
    export NANOBOT_PIP="$VENV_DIR/bin/pip"
}

# ── Clone or update repo ───────────────────────────────────────────────────────
setup_repo() {
    section "Repository"

    REPO_URL="https://github.com/tundefund0-gif/nanobot-lite.git"
    DEST="${HOME}/nanobot-lite"
    USE_EXISTING=0

    if [ -d "$DEST" ]; then
        read -p "nanobot-lite directory exists. Pull latest? [Y/n]: " ans
        case "$ans" in [nN]*) USE_EXISTING=1;; esac
    fi

    if [ $USE_EXISTING -eq 1 ]; then
        success "Keeping existing repo at ${DEST}"
    elif [ -d "$DEST" ]; then
        info "Pulling latest in ${DEST}..."
        cd "$DEST"
        git pull origin main --quiet
        success "Updated"
    else
        info "Cloning nanobot-lite to ${DEST}..."
        git clone --depth=1 "$REPO_URL" "$DEST"
        success "Cloned"
    fi

    export DEST
    cd "$DEST"
}

# ── Install Python dependencies ───────────────────────────────────────────────
install_deps() {
    section "Dependencies"

    DEPS="httpx loguru python-telegram-bot pyyaml aiohttp"

    info "Installing core deps: $DEPS"
    "$NANOBOT_PYTHON" -m pip install --quiet $DEPS

    # Platform-specific
    if [ "$PLATFORM" = "termux" ]; then
        info "Installing Termux-specific deps..."
        "$NANOBOT_PYTHON" -m pip install --quiet termux-api 2>/dev/null || true
    fi

    success "Dependencies installed"
}

# ── Install nanobot-lite package ───────────────────────────────────────────────
install_package() {
    section "Package Install"

    info "Installing nanobot-lite in editable mode..."
    cd "$DEST"
    "$NANOBOT_PYTHON" -m pip install --quiet -e .

    # Install loguru if missing (critical — used everywhere)
    "$NANOBOT_PYTHON" -m pip install --quiet loguru

    success "nanobot-lite installed"
}

# ── Create config if missing ──────────────────────────────────────────────────
setup_config() {
    section "Configuration"

    CONFIG_DIR="${HOME}/.nanobot_lite"
    CONFIG_FILE="${CONFIG_DIR}/config.yaml"

    mkdir -p "$CONFIG_DIR"

    if [ -f "$CONFIG_FILE" ]; then
        success "Config already exists: ${CONFIG_FILE}"
        info "Run 'nanobot-lite setup' to reconfigure."
    else
        info "No config found — running setup wizard..."
        "$NANOBOT_PYTHON" -m nanobot_lite setup
    fi
}

# ── Create convenient shell aliases ──────────────────────────────────────────
setup_alias() {
    section "Shell Alias"

    BASHRC="${HOME}/.bashrc"
    TERMUXRC="${HOME}/.bashrc"
    ZSHRC="${HOME}/.zshrc"

    ALIAS_LINE="alias nanobot='${NANOBOT_PYTHON} -m nanobot_lite'"

    for RC in "$BASHRC" "$ZSHRC" "$TERMUXRC"; do
        [ -f "$RC" ] || continue
        if ! grep -q "nanobot=" "$RC" 2>/dev/null; then
            echo "" >> "$RC"
            echo "# Nanobot-Lite" >> "$RC"
            echo "$ALIAS_LINE" >> "$RC"
            success "Alias added to $RC"
        fi
    done

    info "Run '${BOLD}nanobot${RESET}' to start the bot"
}

# ── Verify installation ────────────────────────────────────────────────────────
verify() {
    section "Verify"

    info "Checking nanobot-lite import..."
    if "$NANOBOT_PYTHON" -c "import nanobot_lite; print(nanobot_lite.__version__)" 2>/dev/null; then
        success "nanobot-lite import OK"
    else
        error "Import failed — check dependencies"
        return 1
    fi

    info "Checking CLI entry point..."
    if "$NANOBOT_PYTHON" -m nanobot_lite --version 2>/dev/null; then
        success "CLI entry point OK"
    else
        warn "CLI version check failed — run 'nanobot-lite run' directly"
    fi

    return 0
}

# ── Print summary ─────────────────────────────────────────────────────────────
summary() {
    section "Install Complete!"

    echo -e "  ${BOLD}Nanobot-Lite${RESET}  ${GREEN}✓${RESET}"
    echo ""
    echo -e "  ${CYAN}Python:${RESET}   ${NANOBOT_PYTHON}"
    echo -e "  ${CYAN}Repo:${RESET}     ${DEST}"
    echo -e "  ${CYAN}Venv:${RESET}     ${VENV_DIR}"
    echo -e "  ${CYAN}Config:${RESET}   ${HOME}/.nanobot_lite/config.yaml"
    echo ""
    echo -e "  ${BOLD}Upgrade:${RESET}  cd ~/nanobot-lite && git pull origin main"
    echo -e "  ${BOLD}Run:${RESET}      nanobot-lite run"
    echo ""
    echo -e "  ${YELLOW}Tip:${RESET} add 'source ~/.bashrc' to activate the nanobot alias"
    echo ""
}

# ── Main ───────────────────────────────────────────────────────────────────────
main() {
    echo ""
    echo -e "${CYAN}${BOLD}"
    echo "  ╔══════════════════════════════════════╗"
    echo "  ║   Nanobot-Lite Bootstrap Installer  ║"
    echo "  ║   curl | bash — no pip required      ║"
    echo "  ╚══════════════════════════════════════╝"
    echo -e "${RESET}"

    bootstrap_python
    setup_venv
    setup_repo
    install_deps
    install_package
    setup_config
    setup_alias

    if verify; then
        summary
    else
        section "Post-Install Fix"
        info "Run the following manually if anything failed:"
        echo "  cd ~/nanobot-lite"
        echo "  ~/nanobot_env/bin/pip install -e ."
        echo "  ~/nanobot_env/bin/python -m nanobot_lite setup"
    fi
}

main "$@"
