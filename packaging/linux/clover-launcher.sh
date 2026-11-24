#!/usr/bin/env bash
# Clover launcher: first run installs Clover (real install.sh), then opens the
# lucky `clover setup`. Later runs just open Clover. Runs in a terminal window.
set -u
HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
SHARE="${CLOVER_PKG_SHARE:-$HERE/../share/clover-installer}"
BRANCH="$(cat "$SHARE/BRANCH" 2>/dev/null || echo main)"
CLOVER_BIN="${CLOVER_HOME:-$HOME/.clover}/../.local/bin/clover"
for c in "$HOME/.local/bin/clover" "$(command -v clover 2>/dev/null)"; do
  [ -n "$c" ] && [ -x "$c" ] && CLOVER_BIN="$c" && break
done
if [ "${1:-}" = "--print-plan" ]; then echo "branch=$BRANCH script=$SHARE/install.sh"; exit 0; fi
if [ ! -x "$CLOVER_BIN" ]; then
  echo "🍀 Installing Clover (this takes a few minutes)..."
  bash "$SHARE/install.sh" --branch "$BRANCH" || { echo; echo "Install did not finish. Press Enter to close."; read -r _; exit 1; }
  exit 0   # install.sh already ran `clover setup`
fi
if [ ! -f "${CLOVER_HOME:-$HOME/.clover}/config.yaml" ]; then exec "$CLOVER_BIN" setup; fi
exec "$CLOVER_BIN"
