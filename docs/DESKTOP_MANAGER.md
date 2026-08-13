# Desktop Manager

## Architecture

The Windows desktop manager is a thin pywebview shell around the existing FastAPI UI. It does not duplicate player,
collection, analytics, permission, or replay logic. `pubg_ai.desktop` resolves a localhost endpoint, verifies an
existing process through `/health`, starts FastAPI only when needed, and opens that URL in a native window.

The browser-only `run-web` mode remains supported. Both modes use the same endpoints, local settings file, MySQL
database, Raw storage, Replay storage, workers, and tests.

## Local Boundary

- Desktop endpoints accept only `127.0.0.1`, `localhost`, or `::1`.
- `0.0.0.0` and remote host names are rejected.
- An occupied port is reused only when its health response identifies the PUBG local manager.
- The JavaScript bridge returns runtime metadata and folder-picker results only. It never returns PUBG or Discord
  secrets.
- Closing a desktop window does not stop a manager process that was already running before the window opened.

## Storage Selection

Desktop mode reveals native folder-picker buttons for Raw, Replay, deletion-backup, and quarantine storage. Selection
updates the form only; the existing Storage Settings API performs validation and persistence after Save. Browser mode
keeps the same text inputs and hides the native-only buttons.

## Launch

```powershell
python -m pip install -e ".[desktop]"
python -m pubg_ai.cli run-desktop --maximized
```

The repository-root `run_desktop.cmd` provides a double-click launcher after installation. The configured
`local_web_base_url` supplies the default desktop port, with `8000` used when no local URL is configured.

## Windows Executable

```powershell
python -m pip install -e ".[desktop-build]"
python -m PyInstaller --clean --noconfirm pubg_ai_desktop.spec
```

This creates `dist/PUBG_AI_Manager.exe` without a console window. The packaged entrypoint searches upward from the
executable and current directory for the project `.env` plus `config` directory. Set `PUBG_AI_BASE_DIR` when the
executable is stored elsewhere. Secrets and local settings are not embedded into the executable.
