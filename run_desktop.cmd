@echo off
setlocal
cd /d "%~dp0"

rem A repository checkout must run its current source. The packaged executable can
rem lag behind after an update and is only a fallback when Python or source is unavailable.
if exist "%~dp0src\pubg_ai\cli.py" (
  where python >nul 2>nul
  if errorlevel 1 goto packaged_app
  set "PYTHONPATH=%~dp0src;%PYTHONPATH%"
  python -m pubg_ai.cli --base-dir "%~dp0." run-desktop --maximized
  if errorlevel 1 goto launch_failed
  exit /b 0
)

:packaged_app
if exist "%~dp0dist\PUBG_AI_Manager.exe" (
  start "" "%~dp0dist\PUBG_AI_Manager.exe"
  exit /b 0
)

:launch_failed
echo.
echo PUBG AI Local Manager failed to start.
echo Install the desktop dependencies with: python -m pip install -e ".[desktop]"
pause
endlocal
