@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" scripts\feedback_admin.py --copy-key
if errorlevel 1 goto failed
start "" "http://127.0.0.1:8617/admin.html"
exit /b 0
:failed
echo Unable to copy the local management key. Check Python and the preview service.
pause
