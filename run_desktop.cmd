@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0dist\PUBG_AI_Manager.exe" (
  start "" "%~dp0dist\PUBG_AI_Manager.exe"
  exit /b 0
)
python -m pubg_ai.cli run-desktop --maximized
if errorlevel 1 (
  echo.
  echo PUBG AI Local Manager failed to start.
  pause
)
endlocal
