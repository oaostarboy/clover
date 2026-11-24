# Clover native installers (2026-11-24, version 2.1.10.2, NOT released)
Source: branch `packaging/native-installers` (on onboarding/tui-first-pass @064861af), repo oaostarboy/clover.
All installers are thin: they ship scripts/install.sh or install.ps1 (the real installer) and
install from git branch `onboarding/tui-first-pass` (BRANCH file inside each package; change it by rebuilding).
They download uv/Python/Node at install time, so they need internet. The mac/linux/windows installs then land the real `clover`.
Not the Electron desktop app (electron-builder config exists in apps/desktop but was not built here).

## Rebuild
- Windows .exe: run workflow "Native installers (manual, no release)" (`gh workflow run native-installers.yml --ref packaging/native-installers -f branch=<git branch>`; also runs on push to that branch). NSIS on windows-latest, then silent-installs and runs `clover --version`. Artifact `windows-exe`. Last run: 45475252394.
- Linux deb/rpm/AppImage: `docker run --rm -v $PWD:/src:ro -v $OUT:/out debian:stable bash -c 'apt-get update -qq && apt-get install -y -qq dpkg rpm curl file && cp -r /src /tmp/r && bash /tmp/r/packaging/linux/build-linux.sh 2.1.10.2 onboarding/tui-first-pass /out'`
- macOS .pkg (needs a Mac): `bash packaging/macos/build-pkg.sh 2.1.10.2 onboarding/tui-first-pass out [Developer ID Installer identity]`

## Signing
- None signed. Mac has 0 code-signing identities; Windows has no cert. Expect Gatekeeper ("unidentified developer", right-click > Open) and SmartScreen ("More info > Run anyway").
- Not notarized (Mac keychain locked, no identity).

## Tested (real runs)
- Windows: Actions run 45475252394 on windows-latest: `/S` silent install from cmd, exit 0, real install.ps1 ran, Start Menu shortcut present, fresh cmd `clover --version` => "Clover Cognition v2.1.10.2". Log: windows-actions-run-45475252394.log. Interactive wizard pages NOT tested (no screenshot).
- Linux .deb: installed with apt in a clean debian:stable container, ran `clover-installer` as a normal user: install rc=0, `clover --version` => v2.1.10.2. Found+fixed 4 missing deps this way (xz, libatomic, build-essential, python3). Note the container run had no TTY, so the `clover setup` screen did not start there.
- Linux .AppImage: ran on this box with a throwaway HOME inside xterm: showed the new CLOVER setup screen (appimage-first-screen.png). Full install via AppImage not run (same install.sh as the deb).
- Linux .rpm: built; `rpm -qp` requires/files checked only. Not installed.
- macOS .pkg: built on the Mac; `pkgutil --check-signature` = no signature; payload + postinstall extracted; postinstall run (as root via sudo) against a throwaway HOME: rc=0, install ok, `clover --version` => v2.1.10.2. NOT run through `installer -pkg` (needs sudo to /) and the Clover.app launcher (opens Terminal) was not clicked. Mac scratch removed; the Mac's own clover was untouched.
- Known gap: npm "browser tools" step can fail on machines without compilers; mac needs Xcode CLT.
