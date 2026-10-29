@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" scripts\feedback_admin.py --copy-key
if errorlevel 1 goto failed
start "" "http://127.0.0.1:8617/feedback-admin.html"
exit /b 0
:failed
echo Unable to open feedback management. Check that Python and the local preview are running.
pause
