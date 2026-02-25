@echo off
TITLE OpenMedia Launcher
cls

echo [#] Checking system readiness...

:: 1. Check if Ollama is running
curl -s http://localhost:11434/ >nul
if %errorlevel% neq 0 (
    echo [!] ERROR: Ollama is not running! 
    echo Please start Ollama and try again.
    pause
    exit /b
)

:: 2. Activate venv and Launch
if exist venv\Scripts\activate (
    echo [#] Activating environment and launching OpenMedia...
    call venv\Scripts\activate
    openmedia
) else (
    echo [!] ERROR: Virtual environment not found. Please run 'python install.py' first.
    pause
)
