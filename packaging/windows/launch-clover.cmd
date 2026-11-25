@echo off
title Clover
set "D=%~dp0"
set /p B=<"%D%BRANCH"
if exist "%LOCALAPPDATA%\clover\clover-c1\venv\Scripts\clover.exe" goto run
echo Installing Clover. This takes a few minutes, please wait...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%D%install.ps1" -Branch %B%
if errorlevel 1 ( echo. & echo Install did not finish. & pause & exit /b 1 )
exit /b 0
:run
"%LOCALAPPDATA%\clover\clover-c1\venv\Scripts\clover.exe" %*
