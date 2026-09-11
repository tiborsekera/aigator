#!/usr/bin/env bash
# Aigator — 1-Line Quick Installer for Linux, macOS, WSL, and Termux
# Usage: curl -fsSL https://raw.githubusercontent.com/tiborsekera/aigator/main/install.sh | bash

set -euo pipefail

BOLD="\033[1m"
GREEN="\033[32m"
CYAN="\033[36m"
YELLOW="\033[33m"
RED="\033[31m"
RESET="\033[0m"

printf "\n${CYAN}${BOLD}🐊 Installing Aigator (Local AI Session Aggregator)...${RESET}\n\n"

# 1. Check for Python 3.10+
PYTHON_BIN=""
for candidate in python3 python python3.14 python3.13 python3.12 python3.11 python3.10; do
    if command -v "$candidate" >/dev/null 2>&1; then
        if "$candidate" -I -c "import sqlite3,sys; assert sys.version_info >= (3,10); sqlite3.connect(':memory:').execute('CREATE VIRTUAL TABLE t USING fts5(x)')" 2>/dev/null; then
            PYTHON_BIN=$("$candidate" -I -c 'import sys; print(sys.executable)')
            break
        fi
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    printf "${RED}Error: Python 3.10 or higher with SQLite FTS5 is required.${RESET}\n"
    printf "Please install Python 3.10+ using your package manager (e.g. 'sudo apt install python3', 'brew install python', or 'pkg install python').\n"
    exit 1
fi

PYTHON_VER=$("$PYTHON_BIN" -I -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')")
printf "  • Found Python: ${BOLD}%s${RESET} (%s)\n" "$PYTHON_BIN" "$PYTHON_VER"

# 2. Check for SQLite FTS5 support
if ! "$PYTHON_BIN" -I -c "import sqlite3; con = sqlite3.connect(':memory:'); con.execute('CREATE VIRTUAL TABLE t USING fts5(x)')" 2>/dev/null; then
    printf "${RED}Error: Python's sqlite3 module lacks FTS5 full-text search support.${RESET}\n"
    exit 1
fi
printf "  • SQLite FTS5 support: ${GREEN}Verified${RESET}\n"

# 3. Setup Install Target Directory
INSTALL_DIR="${AIGATOR_INSTALL_DIR:-$HOME/.local/share/aigator/app}"
BIN_DIR="${AIGATOR_BIN_DIR:-$HOME/.local/bin}"
BIN_DIR=$("$PYTHON_BIN" -I -c 'import os,sys; print(os.path.abspath(os.path.expanduser(sys.argv[1])))' "$BIN_DIR")

INSTALL_DIR=$("$PYTHON_BIN" -I -c 'import os,sys; print(os.path.abspath(os.path.expanduser(sys.argv[1])))' "$INSTALL_DIR")
if [ -L "$INSTALL_DIR" ] || { [ -e "$INSTALL_DIR" ] && [ ! -f "$INSTALL_DIR/.aigator-install" ]; }; then
    echo "Refusing unmarked existing target: $INSTALL_DIR. Choose a new directory; existing files are preserved." >&2
    exit 1
fi
# The launcher must survive replacement of the application directory.
case "$BIN_DIR/" in "$INSTALL_DIR/"*) echo "Bin directory must be outside the application directory." >&2; exit 1 ;; esac
WRAPPER="$BIN_DIR/aigator"
if [ -L "$WRAPPER" ] || { [ -e "$WRAPPER" ] && [ ! -f "$WRAPPER" ]; }; then
    echo "Refusing non-regular launcher: $WRAPPER" >&2; exit 1
fi
if [ -f "$WRAPPER" ]; then
    LEGACY_LINE=$(printf 'AIGATOR_APP_DIR=%q' "$INSTALL_DIR")
    if ! "$PYTHON_BIN" -I -c 'import sys; lines=open(sys.argv[1]).read().splitlines(); sys.exit(0 if "# aigator managed launcher v1" in lines or sys.argv[2] in lines else 1)' "$WRAPPER" "$LEGACY_LINE"; then
        echo "Refusing unrelated launcher: $WRAPPER" >&2; exit 1
    fi
fi
mkdir -p "$(dirname "$INSTALL_DIR")" "$BIN_DIR"
STAGE=$(mktemp -d "$(dirname "$INSTALL_DIR")/.aigator-stage.XXXXXX")
LAUNCH_STAGE=""
trap 'rm -rf -- "$STAGE"; if [ -n "$LAUNCH_STAGE" ]; then rm -f -- "$LAUNCH_STAGE"; fi' EXIT
REPO_URL="https://github.com/tiborsekera/aigator.git"
if command -v git >/dev/null 2>&1; then
    git clone --depth 1 --quiet "$REPO_URL" "$STAGE/app"
else
    mkdir "$STAGE/app"
    curl --retry 3 --connect-timeout 15 --max-time 180 -fsSL "https://github.com/tiborsekera/aigator/archive/refs/heads/main.tar.gz" -o "$STAGE/source.tar.gz"
    tar -xzf "$STAGE/source.tar.gz" -C "$STAGE/app" --strip-components=1
fi
# Isolate imports from the caller's directory and PYTHONPATH.
RUN_CODE='import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); runpy.run_module("aigator.cli",run_name="__main__")'
"$PYTHON_BIN" -I -c "$RUN_CODE" "$STAGE/app" --help >/dev/null
test -f "$STAGE/app/aigator/cli.py"
printf 'aigator managed installation v1\n' > "$STAGE/app/.aigator-install"
# Prepare the launcher on the same filesystem as its final destination.
LAUNCH_STAGE=$(mktemp "$BIN_DIR/.aigator-launcher.XXXXXX")
printf '#!%s\n# aigator managed launcher v1\n' "$(command -v bash)" > "$LAUNCH_STAGE"
printf 'exec %q -I -c %q %q "$@"\n' "$PYTHON_BIN" "$RUN_CODE" "$INSTALL_DIR" >> "$LAUNCH_STAGE"
chmod +x "$LAUNCH_STAGE"
bash -n "$LAUNCH_STAGE"
BACKUP=""
if [ -e "$INSTALL_DIR" ]; then
    BACKUP=$(mktemp -d "$(dirname "$INSTALL_DIR")/.aigator-backup.XXXXXX")
    mv -- "$INSTALL_DIR" "$BACKUP/app"
fi
if ! mv -- "$STAGE/app" "$INSTALL_DIR"; then
    if [ -n "$BACKUP" ]; then mv -- "$BACKUP/app" "$INSTALL_DIR"; fi
    exit 1
fi
if ! mv -f -- "$LAUNCH_STAGE" "$WRAPPER"; then
    mv -- "$INSTALL_DIR" "$STAGE/app"
    if [ -n "$BACKUP" ]; then mv -- "$BACKUP/app" "$INSTALL_DIR"; fi
    echo "Launcher update failed; previous installation restored." >&2
    exit 1
fi
LAUNCH_STAGE=""
if [ -n "$BACKUP" ]; then printf 'Previous installation preserved at %s/app\n' "$BACKUP"; fi

printf "  • Installed binary: ${BOLD}%s${RESET}\n" "$WRAPPER"

# 6. Check PATH
PATH_OK=false
case ":$PATH:" in
    *":$BIN_DIR:"*) PATH_OK=true ;;
esac

printf "\n${GREEN}${BOLD}🎉 Aigator installed successfully!${RESET}\n\n"

if [ "$PATH_OK" = false ]; then
    printf "${YELLOW}Note:${RESET} %s is not in your PATH.\n" "$BIN_DIR"
    printf "For this Bash/Zsh session, run:\n\n  export PATH=%q:\"\$PATH\"\n\n" "$BIN_DIR"
    printf "Add that line to your shell's startup file to keep it. Other shells use different PATH syntax.\n"
fi

printf "Quickstart commands:\n"
printf "  ${CYAN}aigator daemon${RESET}       # Start local web app on http://127.0.0.1:8765\n"
printf "  ${CYAN}aigator copilot${RESET}      # Sync active VS Code Copilot chats\n"
printf "  ${CYAN}aigator search <q>${RESET}   # Fast BM25 keyword search\n"
printf "  ${CYAN}aigator --help${RESET}       # View all CLI commands\n\n"

printf "Browser extension folder: %s/extension\n" "$INSTALL_DIR"
printf "Load it in Chrome/Brave, start aigator daemon, and pair once in the popup.\n"
printf "The installer does not start a daemon or configure login startup.\n"
