@echo off
setlocal enabledelayedexpansion
title CMI Blog Revamp Agent
cd /d "%~dp0"

echo ============================================
echo   CMI Blog Revamp Agent - starting up
echo ============================================
echo.

rem ---------------------------------------------------------------
rem 1. Make sure Python exists. If not, download and silently install
rem    a local copy (no admin prompt, no wizard, no PATH changes needed
rem    elsewhere on the machine since we call it by full path below).
rem ---------------------------------------------------------------
set "PY_DIR=%~dp0pyruntime"
set "PY_EXE=%PY_DIR%\python.exe"

if exist "%PY_EXE%" goto have_python

where python >nul 2>nul
if %errorlevel%==0 (
    for /f "delims=" %%P in ('where python') do (
        set "PY_EXE=%%P"
        goto have_python
    )
)

echo First-time setup: downloading Python (one-time, a few minutes)...
set "PY_INSTALLER=%TEMP%\python-installer.exe"
powershell -NoProfile -Command "Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe' -OutFile '%PY_INSTALLER%'"
if not exist "%PY_INSTALLER%" (
    echo Could not download Python. Check your internet connection and try again.
    pause
    exit /b 1
)

echo Installing Python to %PY_DIR% ...
"%PY_INSTALLER%" /quiet InstallAllUsers=0 PrependPath=0 Include_test=0 Include_launcher=0 TargetDir="%PY_DIR%"
set "INSTALL_EXIT=%errorlevel%"
del "%PY_INSTALLER%" >nul 2>nul

if exist "%PY_EXE%" goto have_python

rem Exit code 1638 means "a version of Python is already installed" -- the installer refuses a
rem second copy of the same version even to a different folder. That Python is just not on PATH
rem (otherwise the "where python" check above would already have found it) -- look for it in the
rem usual per-user and all-users install locations before giving up.
if "%INSTALL_EXIT%"=="1638" (
    echo A Python installation already exists but isn't on PATH -- looking for it...
    for %%D in (
        "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
        "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
        "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
        "C:\Program Files\Python312\python.exe"
        "C:\Program Files\Python311\python.exe"
        "C:\Program Files\Python313\python.exe"
    ) do (
        if exist %%D (
            set "PY_EXE=%%~D"
            goto have_python
        )
    )
)

echo Python installation did not complete. Try running this file again.
pause
exit /b 1

:have_python
echo Using Python: %PY_EXE%
echo.

rem ---------------------------------------------------------------
rem 2. Create the venv once; reused on every future run.
rem ---------------------------------------------------------------
set "VENV_DIR=%~dp0backend\venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"

if exist "%VENV_PY%" goto have_venv

echo First-time setup: creating a private Python environment...
"%PY_EXE%" -m venv "%VENV_DIR%"
if not exist "%VENV_PY%" (
    echo Could not create the Python environment. Try running this file again.
    pause
    exit /b 1
)

:have_venv

rem ---------------------------------------------------------------
rem 3. Install/update dependencies. Safe to run every time -- pip
rem    skips anything already installed, so after the first run this
rem    takes a few seconds.
rem ---------------------------------------------------------------
echo Checking dependencies (first time may take a few minutes to download)...
"%VENV_PY%" -m pip install --quiet --disable-pip-version-check -r "%~dp0backend\requirements.txt"
if errorlevel 1 (
    echo.
    echo Could not install dependencies. Check your internet connection and try again.
    pause
    exit /b 1
)
echo.

rem ---------------------------------------------------------------
rem 4. Start the server in the background and open the browser.
rem ---------------------------------------------------------------
if not exist "%~dp0.env" (
    echo WARNING: no .env file found next to this script -- the app needs one
    echo ^(DB_URL and LLM settings^) to start. Ask whoever gave you this folder for it.
    pause
    exit /b 1
)

echo Starting the server...
start "CMI Blog Revamp Agent - server (do not close while using the app)" /min ^
    "%VENV_PY%" -m uvicorn main:app --host 127.0.0.1 --port 8000 --app-dir "%~dp0backend"

echo Waiting for the server to come up...
set "READY="
for /l %%i in (1,1,30) do (
    powershell -NoProfile -Command "try { (Invoke-WebRequest -Uri 'http://127.0.0.1:8000/health' -UseBasicParsing -TimeoutSec 1).StatusCode } catch { exit 1 }" >nul 2>nul
    if !errorlevel!==0 (
        set "READY=1"
        goto server_ready
    )
    timeout /t 1 /nobreak >nul
)

:server_ready
if not defined READY (
    echo.
    echo The server did not start in time. A window titled "CMI Blog Revamp Agent - server"
    echo should still be open minimized in your taskbar -- check it for an error message.
    pause
    exit /b 1
)

start "" "http://127.0.0.1:8000"

echo.
echo The app is running at http://127.0.0.1:8000 and should have opened in your browser.
echo Keep the minimized "CMI Blog Revamp Agent - server" window open while you use the app.
echo Closing that window stops the app. This window can be closed.
echo.
pause
