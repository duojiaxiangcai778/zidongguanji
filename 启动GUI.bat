@echo off
chcp 65001 >nul
cd /d "%~dp0"
start "" /b "%~dp0.venv\Scripts\pythonw.exe" "%~dp0main.py" %*
