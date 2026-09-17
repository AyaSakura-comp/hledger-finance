#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SKILLS_DIR="${HFIN_SKILLS_DIR:-$HOME/.hermes/skills}"
BIN_DIR="${HFIN_BIN_DIR:-$HOME/.local/bin}"
FORCE=0

usage() {
  cat <<'EOF'
Usage: ./install.sh [--force] [--skills-dir PATH] [--bin-dir PATH]

Install the hledger-finance agent skill and hfin CLI as symbolic links to
this Git checkout. Existing non-symlink installations are preserved unless
--force is explicitly supplied.
EOF
}

while (($#)); do
  case "$1" in
    --force)
      FORCE=1
      shift
      ;;
    --skills-dir)
      [[ $# -ge 2 ]] || { echo "Missing value for --skills-dir" >&2; exit 2; }
      SKILLS_DIR="$2"
      shift 2
      ;;
    --bin-dir)
      [[ $# -ge 2 ]] || { echo "Missing value for --bin-dir" >&2; exit 2; }
      BIN_DIR="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

for command_name in python3 hledger git; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "Required command not found: $command_name" >&2
    exit 1
  }
done

install_link() {
  local source_path="$1"
  local target_path="$2"
  local label="$3"

  if [[ -L "$target_path" ]]; then
    rm -- "$target_path"
  elif [[ -e "$target_path" ]]; then
    if ((FORCE)); then
      rm -rf -- "$target_path"
    else
      echo "$label already exists and is not a symbolic link: $target_path" >&2
      echo "Review it, then rerun with --force to replace it." >&2
      exit 1
    fi
  fi

  ln -s -- "$source_path" "$target_path"
}

mkdir -p -- "$SKILLS_DIR" "$BIN_DIR"
install_link "$PROJECT_ROOT" "$SKILLS_DIR/hledger-finance" "Skill installation"
install_link "$PROJECT_ROOT/scripts/finance.py" "$BIN_DIR/hfin" "CLI installation"

printf 'Installed skill: %s -> %s\n' "$SKILLS_DIR/hledger-finance" "$PROJECT_ROOT"
printf 'Installed CLI:   %s -> %s\n' "$BIN_DIR/hfin" "$PROJECT_ROOT/scripts/finance.py"
printf '\nRun: hfin --help\n'
