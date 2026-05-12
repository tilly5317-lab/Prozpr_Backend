@echo off
REM Double-click this file: opens a new Command Prompt with .venv activated (repo folder).
cd /d "%~dp0"
if not exist ".venv\Scripts\activate.bat" (
  echo No .venv here. First run:  powershell -ExecutionPolicy Bypass -File scripts\install_deps.ps1
  pause
  exit /b 1
)
start "Prozpr Backend venv" cmd /k "call .venv\Scripts\activate.bat"
