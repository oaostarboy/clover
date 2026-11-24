#!/usr/bin/env bash
# Build Clover .deb, .rpm, .AppImage. Run INSIDE a debian/ubuntu container or runner
# (needs dpkg-deb, rpm, curl, file). Usage: build-linux.sh VERSION BRANCH OUTDIR
set -euo pipefail
VER="$1"; BRANCH="$2"; OUT="$3"
R="$(cd "$(dirname "$0")/../.." && pwd)"; P="$R/packaging/linux"
W="$(mktemp -d "${TMPDIR:-/tmp}/cloverpkg.XXXX")"; mkdir -p "$OUT"
stage() { # $1 = root, $2 = prefix (usr)
  install -d "$1/$2/bin" "$1/$2/share/clover-installer" "$1/$2/share/applications" "$1/$2/share/icons/hicolor/512x512/apps"
  install -m755 "$P/clover-launcher.sh" "$1/$2/bin/clover-installer"
  install -m755 "$P/clover-open.sh" "$1/$2/bin/clover-open"
  install -m755 "$R/scripts/install.sh" "$1/$2/share/clover-installer/install.sh"
  echo "$BRANCH" > "$1/$2/share/clover-installer/BRANCH"
  install -m644 "$P/clover.desktop" "$1/$2/share/applications/clover.desktop"
  install -m644 "$R/apps/desktop/assets/icon.png" "$1/$2/share/icons/hicolor/512x512/apps/clover.png"
}
# ---- deb
D="$W/deb"; stage "$D" usr; install -d "$D/DEBIAN"
cat > "$D/DEBIAN/control" <<C
Package: clover
Version: $VER
Architecture: amd64
Maintainer: Clover <support@cloverai.ca>
Depends: bash, curl, git, ca-certificates, xz-utils, tar, libatomic1, build-essential, python3
Recommends: xterm | x-terminal-emulator
Section: utils
Priority: optional
Homepage: https://github.com/oaostarboy/clover-c1
Description: Clover - double-click installer
 Adds a Clover icon to the app menu. First launch installs Clover for your
 user (no root needed) and opens the Clover setup.
C
dpkg-deb --root-owner-group -Zxz --build "$D" "$OUT/clover_${VER}_amd64.deb"
# ---- rpm
if command -v rpmbuild >/dev/null; then
  RT="$W/rpm"; mkdir -p "$RT"/{BUILD,RPMS,SPECS,SOURCES}; stage "$W/rpmroot" usr
  cat > "$RT/SPECS/clover.spec" <<S
Name: clover
Version: $VER
Release: 1
Summary: Clover double-click installer
License: MIT
BuildArch: x86_64
Requires: bash, curl, git, xz, tar, libatomic, gcc-c++, make, python3
AutoReqProv: no
%description
Adds a Clover icon to the app menu. First launch installs Clover for your user.
%install
mkdir -p %{buildroot}; cp -a $W/rpmroot/usr %{buildroot}/
%files
/usr/bin/clover-installer
/usr/bin/clover-open
/usr/share/clover-installer
/usr/share/applications/clover.desktop
/usr/share/icons/hicolor/*/apps/clover.*
S
  rpmbuild --define "_topdir $RT" -bb "$RT/SPECS/clover.spec" >/dev/null
  cp "$RT"/RPMS/*/clover-*.rpm "$OUT/clover-${VER}-1.x86_64.rpm"
fi
# ---- AppImage
A="$W/Clover.AppDir"; stage "$A" usr
cp "$A/usr/share/applications/clover.desktop" "$A/clover.desktop"; sed -i 's/^Terminal=false/Terminal=true/;s#^Exec=.*#Exec=clover-installer#' "$A/clover.desktop"
cp "$R/apps/desktop/assets/icon.png" "$A/clover.png"
cat > "$A/AppRun" <<'AR'
#!/bin/sh
H="$(dirname "$(readlink -f "$0")")"
export CLOVER_PKG_SHARE="$H/usr/share/clover-installer"
exec "$H/usr/bin/clover-installer" "$@"
AR
chmod +x "$A/AppRun"
curl -fsSL -o "$W/appimagetool" https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage
chmod +x "$W/appimagetool"
ARCH=x86_64 "$W/appimagetool" --appimage-extract-and-run --no-appstream "$A" "$OUT/Clover-${VER}-x86_64.AppImage"
rm -rf "$W"; ls -la "$OUT"
