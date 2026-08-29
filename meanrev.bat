@echo off
REM meanrev — single command entry like claude/codex (Windows)
REM Usage: meanrev [--mode auto|hitl] [--thread-id ID] [--symbol SYM] [--dry-run]
REM Requires: pip install -e .  (creates venv\Scripts\meanrev.exe) OR venv activated
REM Fallback: tries venv python, then python on PATH
if exist "%~dp0venv\Scripts\python.exe" (
    "%~dp0venv\Scripts\python.exe" -m backend.cli %*
    exit /b %errorlevel%
)
where python >nul 2>&1 && (
    python -m backend.cli %*
    exit /b %errorlevel%
)
python -m backend.cli %*
