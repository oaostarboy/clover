; Clover Windows installer (NSIS). Thin: ships install.ps1 + launcher, runs the real installer.
Unicode true
!include "MUI2.nsh"
!include "FileFunc.nsh"
!ifndef VERSION
  !define VERSION "0.0.0"
!endif
!ifndef BRANCH
  !define BRANCH "main"
!endif
!ifndef OUTFILE
  !define OUTFILE "Clover-Setup.exe"
!endif
Name "Clover"
OutFile "${OUTFILE}"
InstallDir "$LOCALAPPDATA\Clover Installer"
RequestExecutionLevel user
BrandingText "Clover ${VERSION}"
!define MUI_ICON "clover.ico"
!define MUI_UNICON "clover.ico"
!define MUI_WELCOMEFINISHPAGE_BITMAP "sidebar.bmp"
!define MUI_UNWELCOMEFINISHPAGE_BITMAP "sidebar.bmp"
!define MUI_HEADERIMAGE
!define MUI_HEADERIMAGE_BITMAP "header.bmp"
!define MUI_HEADERIMAGE_RIGHT
!define MUI_WELCOMEPAGE_TITLE "Let's plant some luck"
!define MUI_WELCOMEPAGE_TEXT "Clover is about to move in. It takes a few minutes and needs the internet.$\r$\n$\r$\nWhen it is ready, a window opens and you pick your lucky charm."
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

Section "Install"
  SetOutPath "$INSTDIR"
  File "..\..\scripts\install.ps1"
  File "clover.ico"
  File "launch-clover.cmd"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  CreateDirectory "$SMPROGRAMS\Clover"
  CreateShortcut "$SMPROGRAMS\Clover\Clover.lnk" "$INSTDIR\launch-clover.cmd" "" "$INSTDIR\clover.ico"
  CreateShortcut "$SMPROGRAMS\Clover\Uninstall Clover installer.lnk" "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\CloverInstaller" "DisplayName" "Clover"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\CloverInstaller" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\CloverInstaller" "UninstallString" "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\CloverInstaller" "DisplayIcon" "$INSTDIR\clover.ico"
  FileOpen $0 "$INSTDIR\BRANCH" w
  FileWrite $0 "${BRANCH}"
  FileClose $0
  DetailPrint "Installing Clover. This takes a few minutes, please wait..."
  IfSilent silent interactive
  silent:
    ; unattended: real install, no prompts. Wait for it to finish.
    ExecWait `powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "& '$INSTDIR\install.ps1' -Branch ${BRANCH} -SkipSetup -NonInteractive *> '$INSTDIR\install.log'; exit $$LASTEXITCODE"` $1
    DetailPrint "install.ps1 exit code $1"
    Goto verify
  interactive:
    ; `start /wait` opens a visible console window and waits until it closes,
    ; so this page stays on "Installing Clover..." until the install and the
    ; setup wizard inside that window are done.
    ExecWait '"$SYSDIR\cmd.exe" /c start "Clover" /wait "$INSTDIR\launch-clover.cmd"' $1
    DetailPrint "launcher exit code $1"
  verify:
    ; Do not say "completed" when Clover is not there.
    IfFileExists "$LOCALAPPDATA\clover\clover-c1\venv\Scripts\clover.exe" done failed
  failed:
    SetErrorLevel 1
    IfSilent +2 0
    MessageBox MB_ICONSTOP|MB_OK "Clover did not finish installing. Run this setup again, or see $INSTDIR\\install.log for details."
    Abort "Clover did not finish installing."
  done:
SectionEnd

Section "Uninstall"
  Delete "$SMPROGRAMS\Clover\*.lnk"
  RMDir "$SMPROGRAMS\Clover"
  Delete "$INSTDIR\*.*"
  RMDir "$INSTDIR"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\CloverInstaller"
SectionEnd
