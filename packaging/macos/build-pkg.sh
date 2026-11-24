#!/usr/bin/env bash
# Build Clover-<ver>-macos.pkg. Usage: build-pkg.sh VERSION BRANCH OUTDIR [INSTALLER_SIGN_ID]
set -euo pipefail
VER="$1"; BRANCH="$2"; OUT="$3"; SIGN="${4:-}"
R="$(cd "$(dirname "$0")/../.." && pwd)"; mkdir -p "$OUT"
W="$(mktemp -d "${TMPDIR:-/tmp}/cloverpkg.XXXX")"
APP="$W/root/Applications/Clover.app"; mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources" "$W/root/usr/local/share/clover-installer" "$W/scripts"
cp "$R/apps/desktop/assets/icon.icns" "$APP/Contents/Resources/Clover.icns"
cp "$R/scripts/install.sh" "$W/root/usr/local/share/clover-installer/install.sh"
echo "$BRANCH" > "$W/root/usr/local/share/clover-installer/BRANCH"
cat > "$APP/Contents/Info.plist" <<P
<?xml version="1.0" encoding="UTF-8"?><!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict><key>CFBundleName</key><string>Clover</string><key>CFBundleDisplayName</key><string>Clover</string>
<key>CFBundleIdentifier</key><string>ca.cloverai.clover.launcher</string><key>CFBundleExecutable</key><string>Clover</string>
<key>CFBundleIconFile</key><string>Clover</string><key>CFBundleVersion</key><string>$VER</string><key>CFBundleShortVersionString</key><string>$VER</string>
<key>CFBundlePackageType</key><string>APPL</string><key>LSMinimumSystemVersion</key><string>11.0</string></dict></plist>
P
# App = opens Terminal running clover (setup on first run)
cat > "$APP/Contents/MacOS/Clover" <<'L'
#!/bin/bash
C="$HOME/.local/bin/clover"; [ -x "$C" ] || C="$(command -v clover || true)"
if [ -z "$C" ]; then C="bash /usr/local/share/clover-installer/install.sh --branch $(cat /usr/local/share/clover-installer/BRANCH)"
elif [ ! -f "${CLOVER_HOME:-$HOME/.clover}/config.yaml" ]; then C="$C setup"; fi
CMD="clear; $C"
/usr/bin/osascript -e "tell application \"Terminal\" to activate" -e "tell application \"Terminal\" to do script \"$CMD\""
L
chmod +x "$APP/Contents/MacOS/Clover"
cat > "$W/scripts/postinstall" <<'PI'
#!/bin/bash
# Runs as root. Install Clover for the logged-in user (no root-owned files), then open setup.
U="${CLOVER_PKG_USER:-$(stat -f%Su /dev/console)}"
[ -z "$U" -o "$U" = root ] && exit 0
H="${CLOVER_PKG_HOME:-$(dscl . -read /Users/$U NFSHomeDirectory | awk '{print $2}')}"
S=/usr/local/share/clover-installer
B="$(cat $S/BRANCH)"
LOG="$H/Library/Logs/clover-pkg-install.log"; mkdir -p "$H/Library/Logs"; chown "$U" "$H/Library/Logs" 2>/dev/null
sudo -u "$U" env HOME="$H" bash "$S/install.sh" --branch "$B" --skip-setup >"$LOG" 2>&1
RC=$?
chown "$U" "$LOG" 2>/dev/null
if [ $RC -eq 0 ] && [ -z "${CLOVER_PKG_NO_OPEN:-}" ]; then sudo -u "$U" open /Applications/Clover.app 2>/dev/null; fi
exit 0
PI
chmod +x "$W/scripts/postinstall"
pkgbuild --root "$W/root" --scripts "$W/scripts" --identifier ca.cloverai.clover --version "$VER" --install-location / "$W/clover-component.pkg"
cat > "$W/dist.xml" <<D
<?xml version="1.0" encoding="utf-8"?><installer-gui-script minSpecVersion="1"><title>Clover</title>
<welcome mime-type="text/plain" file="welcome.txt"/><options customize="never" require-scripts="false" hostArchitectures="arm64,x86_64"/>
<domains enable_anywhere="false" enable_currentUserHome="false" enable_localSystem="true"/>
<choices-outline><line choice="c"/></choices-outline><choice id="c" visible="false"><pkg-ref id="ca.cloverai.clover"/></choice>
<pkg-ref id="ca.cloverai.clover" version="$VER">clover-component.pkg</pkg-ref></installer-gui-script>
D
mkdir -p "$W/res"; echo "This sets up Clover for your user. It takes a few minutes and needs the internet. A Terminal window opens at the end to start your lucky setup." > "$W/res/welcome.txt"
FINAL="$OUT/Clover-${VER}-macos.pkg"
if [ -n "$SIGN" ]; then productbuild --distribution "$W/dist.xml" --resources "$W/res" --package-path "$W" --sign "$SIGN" "$FINAL"
else productbuild --distribution "$W/dist.xml" --resources "$W/res" --package-path "$W" "$FINAL"; fi
rm -rf "$W"; ls -la "$FINAL"
