@echo off
setlocal
set "DROSTE_ROOT=%~dp0"
python "%DROSTE_ROOT%core\droste_cli.py" %*
