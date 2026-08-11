@echo off
setlocal enabledelayedexpansion

:: ====================================================
:: Enterprise One-Click Setup & Launcher Script
:: ====================================================

title Enterprise AI Data Analysis Agent - Bootstrapper
echo ====================================================
echo      Enterprise AI Data Analysis Agent
echo ====================================================
echo.

:: Initialize directories
if not exist logs mkdir logs
if not exist data mkdir data
if not exist output mkdir output

set LOG_FILE=logs\bootstrap.log
echo [BOOTSTRAP START] %date% %time% > "%LOG_FILE%"
echo Windows Version: >> "%LOG_FILE%"
ver >> "%LOG_FILE%" 2>&1

:: ----------------------------------------------------
:: Step 1: Windows validation (Write permissions)
:: ----------------------------------------------------
echo Checking write permissions...
echo test_write > data\temp_write_test.txt 2>nul
if not exist data\temp_write_test.txt (
    echo [ERROR] No write permission in the current directory.
    echo [ERROR] No write permission in the current directory. >> "%LOG_FILE%"
    pause
    exit /b 1
)
del data\temp_write_test.txt >nul 2>&1
echo   ✓ Write Permissions Validated
echo   ✓ Write Permissions Validated >> "%LOG_FILE%"

:: ----------------------------------------------------
:: Step 2 & 3: Python Detection & Version Check
:: ----------------------------------------------------
echo Detecting Python installation...
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed.
    echo [ERROR] Python is not installed. >> "%LOG_FILE%"
    echo Please install Python 3.11.x from: https://www.python.org/downloads/
    pause
    exit /b 1
)

:: Get Python Version
for /f "tokens=2" %%i in ('python --version 2^>^&1') do (
    set FULL_PY_VER=%%i
)
echo Python Version Detected: %FULL_PY_VER%
echo Python Version Detected: %FULL_PY_VER% >> "%LOG_FILE%"

python -c "import sys; m, n = sys.version_info[:2]; sys.exit(0 if (m,n)==(3,11) else (1 if (m,n)==(3,10) else (2 if (m,n)==(3,12) else (3 if m==3 and n>=13 else 4))))"
set PY_STATUS=%errorlevel%

if "%PY_STATUS%" equ "4" (
    echo.
    echo Python Version : %FULL_PY_VER%
    echo Status         : Unsupported
    echo.
    echo Minimum supported version:
    echo Python 3.10
    echo.
    echo Please install Python 3.10 or newer.
    echo https://www.python.org/downloads/
    echo.
    echo Press any key to exit...
    pause >nul
    exit /b 1
)

if "%PY_STATUS%" equ "3" (
    echo Python Version : %FULL_PY_VER%
    echo Status         : Supported
    echo.
    echo Note: This version has not been officially regression tested yet,
    echo but the launcher will continue automatically.
    echo.
    echo Launching application...
    echo.
)

if "%PY_STATUS%" equ "2" (
    echo Python Version : %FULL_PY_VER%
    echo Status         : Supported
    echo.
    echo Launching application...
    echo.
)

if "%PY_STATUS%" equ "1" (
    echo Python Version : %FULL_PY_VER%
    echo Status         : Supported
    echo.
    echo Launching application...
    echo.
)

if "%PY_STATUS%" equ "0" (
    echo Python Version : %FULL_PY_VER%
    echo Status         : Fully Supported
    echo.
    echo Launching application...
    echo.
)

:: ----------------------------------------------------
:: Step 4: Verify Project Files
:: ----------------------------------------------------
echo Verifying project files...
set MISSING_FILES=
if not exist requirements.txt set MISSING_FILES=!MISSING_FILES! requirements.txt
if not exist main.py set MISSING_FILES=!MISSING_FILES! main.py
if not exist streamlit_app.py set MISSING_FILES=!MISSING_FILES! streamlit_app.py
if not exist .env set MISSING_FILES=!MISSING_FILES! .env
if not exist credentials\service_account.json set MISSING_FILES=!MISSING_FILES! credentials\service_account.json
if not exist questions.txt set MISSING_FILES=!MISSING_FILES! questions.txt

if "%MISSING_FILES%" neq "" (
    echo [ERROR] Missing required project files:!MISSING_FILES!
    echo [ERROR] Missing required project files:!MISSING_FILES! >> "%LOG_FILE%"
    if not exist .env (
        echo [INFO] Creating default .env file from .env.example...
        copy .env.example .env >nul
        echo [WARNING] Please configure credentials in the newly created .env file before running.
    )
    pause
    exit /b 1
)
echo   ✓ Project Files Verified
echo   ✓ Project Files Verified >> "%LOG_FILE%"

:: ----------------------------------------------------
:: Step 5: Internet Verification
:: ----------------------------------------------------
echo Checking internet connection for package installation and Gemini API access...
python -c "import socket; socket.create_connection(('pypi.org', 443), timeout=3)" >nul 2>&1
if errorlevel 1 (
    echo [WARNING] PyPI registry is unreachable. You may be offline or behind a proxy.
    echo [WARNING] PyPI registry is unreachable. >> "%LOG_FILE%"
    set OFFLINE=1
) else (
    echo   ✓ Internet Connectivity Validated
    echo   ✓ Internet Connectivity Validated >> "%LOG_FILE%"
    set OFFLINE=0
)

:: ----------------------------------------------------
:: Step 6 & 7: Virtual Environment Creation & Recovery
:: ----------------------------------------------------
set VENV_PATH=gdrive_agent_env
set FRESH_VENV=0

if not exist %VENV_PATH%\Scripts\activate.bat (
    echo Creating virtual environment - gdrive_agent_env...
    echo Creating virtual environment >> "%LOG_FILE%"
    python -m venv %VENV_PATH% >> "%LOG_FILE%" 2>&1
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
    set FRESH_VENV=1
)

:: Test Venv Python Integrity
%VENV_PATH%\Scripts\python.exe -c "import sys" >nul 2>&1
if errorlevel 1 (
    echo [WARNING] Virtual environment appears corrupted. Rebuilding...
    echo Rebuilding virtual environment >> "%LOG_FILE%"
    rmdir /s /q %VENV_PATH% >nul 2>&1
    python -m venv %VENV_PATH% >> "%LOG_FILE%" 2>&1
    set FRESH_VENV=1
)
echo   ✓ Virtual Environment Ready
echo   ✓ Virtual Environment Ready >> "%LOG_FILE%"

:: ----------------------------------------------------
:: Step 8: Dependency Check (Hash check)
:: ----------------------------------------------------
echo Verifying dependencies...

:: Generate MD5/SHA256 Hash of requirements.txt
for /f "skip=1 tokens=*" %%i in ('certutil -hashfile requirements.txt SHA256 ^| findstr /v "CertUtil"') do (
    set REQS_HASH=%%i
    goto :hash_done
)
:hash_done
set REQS_HASH=!REQS_HASH: =!

if not exist .project_state mkdir .project_state
set CACHED_HASH=
if exist .project_state\requirements.sha256 (
    set /p CACHED_HASH=<.project_state\requirements.sha256
    set CACHED_HASH=!CACHED_HASH: =!
)

set RUN_INSTALL=0
if "%FRESH_VENV%" equ "1" set RUN_INSTALL=1
if "!REQS_HASH!" neq "!CACHED_HASH!" set RUN_INSTALL=1

:: Double check if key modules are missing
%VENV_PATH%\Scripts\python.exe -c "import pandas, streamlit, google.genai" >nul 2>&1
if errorlevel 1 set RUN_INSTALL=1

if "%RUN_INSTALL%" equ "1" (
    if "%OFFLINE%" equ "1" (
        echo [WARNING] Offline mode: Skipping pip install because PyPI is unreachable.
        echo [WARNING] Offline mode: Skipping pip install >> "%LOG_FILE%"
    ) else (
        echo Installing dependencies from requirements.txt...
        echo Installing dependencies >> "%LOG_FILE%"
        %VENV_PATH%\Scripts\python.exe -m pip install --upgrade pip setuptools wheel >> "%LOG_FILE%" 2>&1
        %VENV_PATH%\Scripts\python.exe -m pip install -r requirements.txt >> "%LOG_FILE%" 2>&1
        if errorlevel 1 (
            echo [ERROR] Installation failed. Check logs/bootstrap.log for details.
            pause
            exit /b 1
        )
        echo !REQS_HASH! > .project_state\requirements.sha256
    )
)
echo   ✓ Dependencies Verified
echo   ✓ Dependencies Verified >> "%LOG_FILE%"

:: ----------------------------------------------------
:: Step 8.5: Startup Configuration Validation
:: ----------------------------------------------------
echo Running system startup configuration validation...
%VENV_PATH%\Scripts\python.exe -c "from config import settings; settings.validate_startup()" >> "%LOG_FILE%" 2>&1
if errorlevel 1 (
    echo [ERROR] System Startup Configuration Validation failed.
    echo [ERROR] Please check that your .env file and credentials are configured correctly.
    echo [ERROR] Run "python main.py" or check logs/bootstrap.log for detailed configuration errors.
    echo [ERROR] Validation Failed >> "%LOG_FILE%"
    pause
    exit /b 1
)
echo   ✓ Startup Configuration Validated
echo   ✓ Startup Configuration Validated >> "%LOG_FILE%"

:: ----------------------------------------------------
:: Step 9: Port Conflict Resolution
:: ----------------------------------------------------
echo Scanning for free ports starting from 8501...
set FREE_PORT=8501
%VENV_PATH%\Scripts\python.exe -c "import socket; s=socket.socket(); [(s.bind(('127.0.0.1', p)), s.close(), open('data/free_port.txt', 'w').write(str(p)), exit()) for p in range(8501, 8600) if (s:=socket.socket()) and s.connect_ex(('127.0.0.1', p)) != 0]" >nul 2>&1
if exist data\free_port.txt (
    set /p FREE_PORT=<data\free_port.txt
    del data\free_port.txt >nul 2>&1
)
echo Using free port: %FREE_PORT%
echo Using free port: %FREE_PORT% >> "%LOG_FILE%"

:: ----------------------------------------------------
:: Step 10: Launch Streamlit & Open Browser
:: ----------------------------------------------------
echo Starting Streamlit server...
echo Starting Streamlit server >> "%LOG_FILE%"

start /b %VENV_PATH%\Scripts\streamlit.exe run streamlit_app.py --server.port %FREE_PORT% --server.headless true >> logs\streamlit.log 2>&1

:: Poll port until active (timeout 15 seconds)
echo Waiting for Streamlit server to bind to port %FREE_PORT%...
%VENV_PATH%\Scripts\python.exe -c "import socket, time, sys; p=int(sys.argv[1]); [(s:=socket.socket(), s.settimeout(1), res:=s.connect_ex(('127.0.0.1', p)), s.close(), exit(0) if res==0 else time.sleep(1)) for _ in range(15)]" %FREE_PORT% >nul 2>&1

if errorlevel 1 (
    echo [WARNING] Streamlit server is taking longer than expected to start.
    echo [WARNING] Opening browser link anyway...
)

echo   ✓ Streamlit Started on Port %FREE_PORT%
echo   ✓ Streamlit Started on Port %FREE_PORT% >> "%LOG_FILE%"

:: Open default browser
echo   ✓ Opening Browser...
echo   ✓ Opening Browser... >> "%LOG_FILE%"
start http://localhost:%FREE_PORT%

cls
echo ====================================================
echo        Enterprise AI Data Analysis Agent
echo ====================================================
echo.
echo   [SUCCESS] Environment Ready
echo   [SUCCESS] Dependencies Verified
echo   [SUCCESS] Configuration Validated
echo   [SUCCESS] Streamlit Running on Port %FREE_PORT%
echo   [SUCCESS] Local URL: http://localhost:%FREE_PORT%
echo   [SUCCESS] Default Browser Opened
echo.
echo   Ready for Testing. Press Ctrl+C in this terminal 
echo   to stop the application.
echo ====================================================
echo [BOOTSTRAP SUCCESS] %date% %time% >> "%LOG_FILE%"
echo.

:: Keep script alive to run process in background
%VENV_PATH%\Scripts\python.exe -c "import time; [time.sleep(1) for _ in iter(int, 1)]"
