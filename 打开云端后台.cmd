@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" scripts\feedback_admin.py --key-file local_state\cloud\feedback-admin.key --copy-key
if errorlevel 1 goto failed
start "" "https://zhixuanji.cn/admin.html"
exit /b 0
:failed
echo Cloud management key is unavailable. Retrieve it privately from the server first.
pause
