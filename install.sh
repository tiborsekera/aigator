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
for candidate in python3 python python3.13 python3.12 python3.11 python3.10; do
    if command -v "$candidate" >/dev/null 2>&1; then
        if "$candidate" -c "import sys; exit(0 if sys.version_info >= (3, 10) else 1)" 2>/dev/null; then
            PYTHON_BIN="$candidate"
            break
        fi
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    printf "${RED}Error: Python 3.10 or higher is required.${RESET}\n"
    printf "Please install Python 3.10+ using your package manager (e.g. 'sudo apt install python3', 'brew install python', or 'pkg install python').\n"
    exit 1
fi

PYTHON_VER=$("$PYTHON_BIN" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')")
printf "  • Found Python: ${BOLD}%s${RESET} (%s)\n" "$PYTHON_BIN" "$PYTHON_VER"

# 2. Check for SQLite FTS5 support
if ! "$PYTHON_BIN" -c "import sqlite3; con = sqlite3.connect(':memory:'); con.execute('CREATE VIRTUAL TABLE t USING fts5(x)')" 2>/dev/null; then
    printf "${RED}Error: Python's sqlite3 module lacks FTS5 full-text search support.${RESET}\n"
    exit 1
fi
printf "  • SQLite FTS5 support: ${GREEN}Verified${RESET}\n"

# 3. Setup Install Target Directory
INSTALL_DIR="${AIGATOR_INSTALL_DIR:-$HOME/.local/share/aigator/app}"
BIN_DIR="${AIGATOR_BIN_DIR:-$HOME/.local/bin}"

INSTALL_DIR=$("$PYTHON_BIN" -c 'import os,sys; print(os.path.abspath(os.path.expanduser(sys.argv[1])))' "$INSTALL_DIR")
if [ -L "$INSTALL_DIR" ] || { [ -e "$INSTALL_DIR" ] && [ ! -f "$INSTALL_DIR/.aigator-install" ]; }; then
    echo "Refusing unmarked existing target: $INSTALL_DIR. Choose a new directory; existing files are preserved." >&2
    exit 1
fi
mkdir -p "$(dirname "$INSTALL_DIR")" "$BIN_DIR"
STAGE=$(mktemp -d "$(dirname "$INSTALL_DIR")/.aigator-stage.XXXXXX")
trap 'rm -rf -- "$STAGE"' EXIT
REPO_URL="https://github.com/tiborsekera/aigator.git"
if command -v git >/dev/null 2>&1; then
    git clone --depth 1 --quiet "$REPO_URL" "$STAGE/app"
else
    mkdir "$STAGE/app"
    curl -fsSL "https://github.com/tiborsekera/aigator/archive/refs/heads/main.tar.gz" -o "$STAGE/source.tar.gz"
    tar -xzf "$STAGE/source.tar.gz" -C "$STAGE/app" --strip-components=1
fi
(cd "$STAGE/app" && "$PYTHON_BIN" -m aigator.cli --help >/dev/null)
test -f "$STAGE/app/aigator/cli.py"
printf 'aigator managed installation v1\n' > "$STAGE/app/.aigator-install"
BACKUP=""
if [ -e "$INSTALL_DIR" ]; then
    BACKUP=$(mktemp -d "$(dirname "$INSTALL_DIR")/.aigator-backup.XXXXXX")
    mv -- "$INSTALL_DIR" "$BACKUP/app"
fi
if ! mv -- "$STAGE/app" "$INSTALL_DIR"; then
    if [ -n "$BACKUP" ]; then mv -- "$BACKUP/app" "$INSTALL_DIR"; fi
    exit 1
fi
if [ -n "$BACKUP" ]; then printf 'Previous installation preserved at %s/app\n' "$BACKUP"; fi

# 5. Create executable wrapper in user bin directory
WRAPPER="$BIN_DIR/aigator"
printf '#!/usr/bin/env bash\nAIGATOR_APP_DIR=%q\n' "$INSTALL_DIR" > "$WRAPPER"
cat << 'EOF' >> "$WRAPPER"

# Find compatible python
PYTHON_EXEC=""
for py in python3 python python3.13 python3.12 python3.11 python3.10; do
    if command -v "$py" >/dev/null 2>&1; then
        if "$py" -c "import sys; exit(0 if sys.version_info >= (3, 10) else 1)" 2>/dev/null; then
            PYTHON_EXEC="$py"
            break
        fi
    fi
done

if [ -z "$PYTHON_EXEC" ]; then
    echo "Error: Python 3.10+ not found in PATH." >&2
    exit 1
fi

PYTHONPATH="$AIGATOR_APP_DIR:$PYTHONPATH" exec "$PYTHON_EXEC" -m aigator.cli "$@"
EOF

chmod +x "$WRAPPER"
printf "  • Installed binary: ${BOLD}%s${RESET}\n" "$WRAPPER"

# 6. Check PATH
PATH_OK=false
case ":$PATH:" in
    *":$BIN_DIR:"*) PATH_OK=true ;;
esac

printf "\n${GREEN}${BOLD}🎉 Aigator installed successfully!${RESET}\n\n"

if [ "$PATH_OK" = false ]; then
    printf "${YELLOW}Note:${RESET} %s is not in your PATH.\n" "$BIN_DIR"
    printf "Add it to your shell configuration by running:\n\n"
    if [ -n "${ZSH_VERSION:-}" ] || [ -f "$HOME/.zshrc" ]; then
        printf "  echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.zshrc && source ~/.zshrc\n\n"
    else
        printf "  echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.bashrc && source ~/.bashrc\n\n"
    fi
fi

printf "Quickstart commands:\n"
printf "  ${CYAN}aigator daemon${RESET}       # Start local web app on http://127.0.0.1:8765\n"
printf "  ${CYAN}aigator copilot${RESET}      # Sync active VS Code Copilot chats\n"
printf "  ${CYAN}aigator search <q>${RESET}   # Fast BM25 keyword search\n"
printf "  ${CYAN}aigator --help${RESET}       # View all CLI commands\n\n"
