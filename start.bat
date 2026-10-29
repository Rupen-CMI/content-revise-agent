@echo off
setlocal enabledelayedexpansion
title CMI Blog Revamp Agent
cd /d "%~dp0"

rem APP_PORT / APP_NO_BROWSER exist so the launcher can be tested without touching a copy that is
rem already running; a normal double-click uses port 8000 and opens the browser.
if not defined APP_PORT set "APP_PORT=8000"

rem Startup log: logs\startup-YYYY-MM-DD.log, a new file each day (launches on the same day append).
rem The server's own output goes to logs\server-YYYY-MM-DD.log and pip's to logs\pip-YYYY-MM-DD.log.
rem If these cannot be written (read-only folder) logging is skipped and the launcher still runs.
if not exist "%~dp0logs" mkdir "%~dp0logs" >nul 2>nul
for /f %%D in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set "TODAY=%%D"
if not defined TODAY set "TODAY=undated"
set "LOGFILE=%~dp0logs\startup-%TODAY%.log"
set "PIP_LOG=%~dp0logs\pip-%TODAY%.log"
set "SERVER_LOG=%~dp0logs\server-%TODAY%.log"
for /f "delims=" %%V in ('ver') do set "OSVER=%%V"
(>>"%LOGFILE%" echo.) 2>nul
call :log "===== launch ====="
call :log "folder: %~dp0"
call :log "user: %USERNAME% on %COMPUTERNAME%, port %APP_PORT%"
call :log "windows: %OSVER%"

echo ============================================
echo   CMI Blog Revamp Agent - starting up
echo ============================================
echo   Log: %LOGFILE%
echo.

rem ==== PACKAGING BLOCK-START (replaced in the package) ====
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
call :log "using python: %PY_EXE%"

rem ---------------------------------------------------------------
rem 2. Create the venv once; reused on every future run.
rem ---------------------------------------------------------------
set "VENV_DIR=%~dp0backend\venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"

if exist "%VENV_PY%" goto have_venv

echo First-time setup: creating a private Python environment...
set "FIRST_RUN=1"
"%PY_EXE%" -m venv "%VENV_DIR%"
if not exist "%VENV_PY%" (
    echo Could not create the Python environment. Try running this file again.
    pause
    exit /b 1
)

:have_venv

rem ---------------------------------------------------------------
rem 3. Install/update dependencies. Safe to run every time -- pip
rem    skips anything already installed. The first install shows pip's live download
rem    progress (it takes several minutes, and a silent window looks frozen); later runs
rem    are quiet so the window isn't flooded with "already satisfied" lines.
rem ---------------------------------------------------------------
set "PIP_FLAGS=--quiet"
if defined FIRST_RUN (
    set "PIP_FLAGS="
    echo Installing components for the first time - this takes several minutes, progress below...
    echo.
) else (
    echo Checking components...
)
"%VENV_PY%" -m pip install %PIP_FLAGS% --disable-pip-version-check --log "%PIP_LOG%" -r "%~dp0backend\requirements.txt"
if errorlevel 1 (
    echo.
    call :say "Could not install dependencies. Check your internet connection and try again."
    call :log "pip failed, details: %PIP_LOG%"
    pause
    exit /b 1
)
call :log "components installed or already present"
echo.
rem ==== PACKAGING BLOCK-END ====

rem ---------------------------------------------------------------
rem 4. Start the server in the background and open the browser.
rem ---------------------------------------------------------------
if not exist "%~dp0.env" (
    echo WARNING: no .env file found next to this script -- the app needs one
    echo ^(DB_URL and LLM settings^) to start. Ask whoever gave you this folder for it.
    call :log "no .env file next to start.bat"
    pause
    exit /b 1
)

rem The language model used to match your article to CMI reports lives in a per-user cache outside
rem this folder. Checked on every launch: instant and silent when it is already there, downloaded
rem (with progress) only when it is missing. Not fatal if it fails -- the app falls back to
rem text matching -- so a missing connection doesn't stop you from starting.
call :log "checking the language model"
"%VENV_PY%" -c "import sys; sys.path.insert(0, r'%~dp0backend'); import reports; reports.ensure_embedding_model(say=print)"
if errorlevel 1 (
    echo.
    echo WARNING: could not download the language model. The app will still run, but matching
    echo reports to your article will be less accurate. Check the internet connection and run
    echo this file again to retry.
    echo.
    call :log "language model: could not be downloaded or loaded (app falls back to text matching)"
) else (
    call :log "language model: ready"
)

rem Every run starts a fresh server: stop a previous copy of THIS app still holding the port
rem (e.g. the browser tab was closed long ago on an older version). Something else on the port is
rem not ours to kill -- exit code 2 tells us to stop and say so.
echo Closing any previous copy of the app...
powershell -NoProfile -Command "$c = Get-NetTCPConnection -LocalPort %APP_PORT% -State Listen -ErrorAction SilentlyContinue; foreach ($x in $c) { $p = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $x.OwningProcess); if ($p.CommandLine -like '*main:app*' -and $p.CommandLine -like '*%~dp0backend*') { taskkill /PID $x.OwningProcess /T /F | Out-Null } else { exit 2 } }; exit 0"
if errorlevel 2 (
    echo.
    call :say "Port %APP_PORT% is being used by another program, not by this app."
    echo Close that program and run this file again.
    pause
    exit /b 1
)
ping -n 2 127.0.0.1 >nul

call :say "Starting the server..."
set "CMI_AUTOSHUTDOWN=1"
rem The server window shows the live output AND the same lines are appended to the server log (UTF-8;
rem Tee-Object would write UTF-16, unreadable in most viewers).
rem PYTHONUNBUFFERED makes python flush every line through the pipe so the window is live, not delayed.
set "PYTHONUNBUFFERED=1"
rem The pipe and redirect are passed in variables (expanded late with !..!) so THIS script does not try to
rem act on them; the new window's own cmd does.
set "PIPE=|"
set "ERR2OUT=2>&1"
(>>"%SERVER_LOG%" echo ===== server start %TODAY% %time:~0,8% =====) 2>nul
start "CMI Blog Revamp Agent - server (do not close while using the app)" /min cmd /c ""%VENV_PY%" -m uvicorn main:app --host 127.0.0.1 --port %APP_PORT% --app-dir "%~dp0backend" !ERR2OUT! !PIPE! powershell -NoProfile -Command "$input !PIPE! ForEach-Object { $_; Add-Content -LiteralPath '%SERVER_LOG%' -Value $_ -Encoding UTF8 }""

call :say "Waiting for the server to come up..."
set "READY="
for /l %%i in (1,1,30) do (
    powershell -NoProfile -Command "try { (Invoke-WebRequest -Uri 'http://127.0.0.1:%APP_PORT%/health' -UseBasicParsing -TimeoutSec 1).StatusCode } catch { exit 1 }" >nul 2>nul
    if !errorlevel!==0 (
        set "READY=1"
        goto server_ready
    )
    ping -n 2 127.0.0.1 >nul
)

:server_ready
if not defined READY (
    echo.
    call :say "The server did not start in time. Its output is saved in:"
    call :say "%SERVER_LOG%"
    echo A window titled "CMI Blog Revamp Agent - server" may still be open minimized
    echo in your taskbar too. Send both log files from the logs folder to get help.
    pause
    exit /b 1
)

call :log "server is up on port %APP_PORT%"
if not defined APP_NO_BROWSER start "" "http://127.0.0.1:%APP_PORT%"

echo.
echo The app is running at http://127.0.0.1:%APP_PORT% and should have opened in your browser.
echo When you close the browser tab the app shuts itself down a few seconds later.
echo Running this file again always starts a fresh copy.
echo.
ping -n 7 127.0.0.1 >nul
exit /b 0

rem ---- helpers -------------------------------------------------------------------------------
rem :log writes one timestamped line to the startup log; :say also prints it. Messages are passed
rem through a variable with delayed expansion so characters like & ( ) in them cannot break the line.
:log
set "LOGMSG=%~1"
(>>"%LOGFILE%" echo [%TODAY% %time:~0,8%] !LOGMSG!) 2>nul
exit /b 0

:say
set "LOGMSG=%~1"
echo(!LOGMSG!
(>>"%LOGFILE%" echo [%TODAY% %time:~0,8%] !LOGMSG!) 2>nul
exit /b 0
