@echo off
chcp 65001 >nul
cd /d "%~dp0"

if exist "%~dp0.venv\Scripts\pythonw.exe" (
  start "" /b "%~dp0.venv\Scripts\pythonw.exe" "%~dp0app.py" %*
  exit /b
)

where pyw >nul 2>nul
if not errorlevel 1 (
  start "" /b pyw -3.11 "%~dp0app.py" %*
  exit /b
)

where pythonw >nul 2>nul
if not errorlevel 1 (
  start "" /b pythonw "%~dp0app.py" %*
  exit /b
)

echo No Python found. Install Python 3.9+ and enable Add to PATH.
pause
