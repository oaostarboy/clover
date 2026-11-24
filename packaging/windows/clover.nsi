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
!define MUI_ICON "..\..\apps\desktop\assets\icon.ico"
!define MUI_UNICON "..\..\apps\desktop\assets\icon.ico"
!define MUI_WELCOMEPAGE_TITLE "Welcome to Clover"
!define MUI_WELCOMEPAGE_TEXT "This sets up Clover on your PC. It takes a few minutes and needs the internet.$\r$\n$\r$\nWhen the files are in place, a window opens to finish the install and start your lucky setup."
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

Section "Install"
  SetOutPath "$INSTDIR"
  File "..\..\scripts\install.ps1"
  File "..\..\apps\desktop\assets\icon.ico"
  File "launch-clover.cmd"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  CreateDirectory "$SMPROGRAMS\Clover"
  CreateShortcut "$SMPROGRAMS\Clover\Clover.lnk" "$INSTDIR\launch-clover.cmd" "" "$INSTDIR\icon.ico"
  CreateShortcut "$SMPROGRAMS\Clover\Uninstall Clover installer.lnk" "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\CloverInstaller" "DisplayName" "Clover"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\CloverInstaller" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\CloverInstaller" "UninstallString" "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\CloverInstaller" "DisplayIcon" "$INSTDIR\icon.ico"
  FileOpen $0 "$INSTDIR\BRANCH" w
  FileWrite $0 "${BRANCH}"
  FileClose $0
  IfSilent silent interactive
  silent:
    ; unattended: real install, no prompts
    ExecWait 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\install.ps1" -Branch ${BRANCH} -SkipSetup -NonInteractive' $1
    DetailPrint "install.ps1 exit code $1"
    Goto done
  interactive:
    ExecWait 'cmd.exe /c ""$INSTDIR\launch-clover.cmd""' $1
  done:
SectionEnd

Section "Uninstall"
  Delete "$SMPROGRAMS\Clover\*.lnk"
  RMDir "$SMPROGRAMS\Clover"
  Delete "$INSTDIR\*.*"
  RMDir "$INSTDIR"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\CloverInstaller"
SectionEnd
