@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "REPO_ROOT=%SCRIPT_DIR%..\..\.."

if not defined DROSTE_MEMORY_ROOT set "DROSTE_MEMORY_ROOT=%REPO_ROOT%"
if not defined DROSTE_VISUALIZER_URL set "DROSTE_VISUALIZER_URL=http://127.0.0.1:5000"
set "PYTHONUNBUFFERED=1"

set "CODEX_PYTHON=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if exist "%CODEX_PYTHON%" (
  "%CODEX_PYTHON%" "%SCRIPT_DIR%droste_codex_mcp.py"
  exit /b %ERRORLEVEL%
)

python "%SCRIPT_DIR%droste_codex_mcp.py"
exit /b %ERRORLEVEL%
