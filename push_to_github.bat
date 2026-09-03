@echo off
cd /d "%~dp0"
"%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe" push origin main
echo.
echo Push finished. If GitHub asks for sign-in, complete it and run this file again.
pause
