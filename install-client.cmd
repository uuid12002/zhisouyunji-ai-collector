@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-client.ps1"
if errorlevel 1 (
  echo Installation failed. Close the collector and retry. See the message above.
  pause
)
