@echo off
rem 免打包直接运行（前提：已装 Python 3.10+ 且执行过 pip install -r requirements.txt）
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m wxreply gui %*
) else (
  python -m wxreply gui %*
)
if errorlevel 1 pause
