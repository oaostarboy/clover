#!/usr/bin/env bash
# Desktop entry target: find a terminal emulator and run the launcher in it.
L="$(dirname "$(readlink -f "$0")")/clover-installer"
[ -t 1 ] && exec "$L" "$@"
for t in x-terminal-emulator gnome-terminal konsole xfce4-terminal kitty alacritty xterm; do
  command -v "$t" >/dev/null 2>&1 || continue
  case "$t" in gnome-terminal|xfce4-terminal) exec "$t" -- "$L";; *) exec "$t" -e "$L";; esac
done
echo "No terminal emulator found; run: clover-installer" >&2; exit 1
