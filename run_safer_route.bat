@echo off
echo Starting Safer Route AI...

:: Start Backend
start cmd /k "cd /d \"C:\Users\USER\Desktop\Safer Route Ai (new)\" && python main.py"

:: Start Frontend
start cmd /k "cd /d \"C:\Users\USER\Desktop\safer-route-dashboard\" && npm run dev"

echo Backend and Frontend are starting in separate windows.
pause
