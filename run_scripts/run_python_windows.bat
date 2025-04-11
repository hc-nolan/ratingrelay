@echo off
cd /d "%~dp0..\"

if exist "%SystemRoot%\System32\where.exe" (
    where uv >nul 2>&1
    if %errorlevel% equ 0 (
        uv run ratingrelay.py
    ) else (
        echo "uv not installed. Attempting to use local venv."
        .venv\Scripts\python ratingrelay.py
    )
) else (
    echo "Command 'where' not found, cannot check for 'uv'."
    exit /b 1
)

