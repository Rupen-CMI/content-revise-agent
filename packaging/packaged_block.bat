rem ---------------------------------------------------------------
rem 1. Python ships inside the package (pyruntime). It ignores any
rem    Python on the machine and needs no admin rights.
rem ---------------------------------------------------------------
set "PY_DIR=%~dp0pyruntime"
set "PY_EXE=%PY_DIR%\python.exe"
if not exist "%PY_EXE%" (
    call :say "This package is incomplete: the pyruntime folder is missing."
    echo Re-extract the whole zip, keeping all files together, and try again.
    pause
    exit /b 1
)
echo Using the bundled Python: %PY_EXE%
echo.
call :log "bundled python: %PY_EXE%"
"%PY_EXE%" --version >>"%LOGFILE%" 2>&1

rem Torch (part of the components) cannot load with a too-old Microsoft Visual C++ runtime (WinError
rem 1114 on c10.dll). The package ships a current copy next to python.exe, which Windows prefers over
rem the one in System32. Record both so a failure on some other PC can be diagnosed from the log.
set "VC_MISSING="
if not exist "%PY_DIR%\msvcp140.dll" set "VC_MISSING=1"
if not exist "%PY_DIR%\vcruntime140_1.dll" set "VC_MISSING=1"
if defined VC_MISSING (
    call :log "Visual C++ runtime: the bundled copy is MISSING from pyruntime"
) else (
    call :log "Visual C++ runtime: bundled copy present in pyruntime"
)
for /f "delims=" %%V in ('powershell -NoProfile -Command "(Get-Item $env:SystemRoot\System32\msvcp140.dll).VersionInfo.FileVersion" 2^>nul') do call :log "System32 msvcp140.dll version: %%V"
for /f "delims=" %%V in ('powershell -NoProfile -Command "(Get-CimInstance Win32_Processor).Name" 2^>nul') do call :log "cpu: %%V"

rem ---------------------------------------------------------------
rem 2. First launch only: install the app's components once, directly
rem    into the bundled runtime (no venv). The marker file below makes
rem    every later launch skip straight past this in a few seconds.
rem ---------------------------------------------------------------
set "REQS=%~dp0backend\requirements.txt"
set "DEPS_MARKER=%PY_DIR%\.deps_done"
set "CHECK_OUT=%~dp0logs\component-check.tmp"
if not exist "%REQS%" (
    call :say "This package is incomplete: backend\requirements.txt is missing."
    echo Re-extract the whole zip, keeping all files together, and try again.
    pause
    exit /b 1
)
if exist "%DEPS_MARKER%" (
    call :say "Checking components: already installed, nothing to download."
) else (
    call :say "First-time setup: downloading and installing components..."
    echo Needs internet. This can take a few minutes - progress shows below.
    echo.
    call :log "pip install started, details in %PIP_LOG%"
    "%PY_EXE%" -m pip install --no-input --only-binary=:all: --retries 5 --timeout 60 --disable-pip-version-check --no-warn-script-location --log "%PIP_LOG%" -r "%REQS%"
    if errorlevel 1 (
        echo.
        call :say "Could not download the components. Please check your internet"
        echo connection ^(or a firewall blocking the downloads^) and run this
        echo file again. Nothing was installed yet, so nothing is broken.
        call :log "pip install FAILED, details in %PIP_LOG%"
        pause
        exit /b 1
    )
    call :log "pip install finished"
    rem Verify what was downloaded before marking the runtime "complete",
    rem so a half-broken install never gets silently skipped later. The python error text is kept
    rem in the log (and shown on screen if it fails) instead of being thrown away.
    "%PY_EXE%" -c "import fastapi, uvicorn, openai, httpx, trafilatura, bs4, docx, ddgs, dotenv, feedparser, sentence_transformers; print('components ok')" >"%CHECK_OUT%" 2>&1
    set "CHECK_EXIT=!errorlevel!"
    call :log "component check exit code: !CHECK_EXIT!"
    (>>"%LOGFILE%" type "%CHECK_OUT%") 2>nul
    if not "!CHECK_EXIT!"=="0" (
        echo.
        call :say "The components were downloaded but did not load correctly."
        echo The error was:
        type "%CHECK_OUT%"
        echo.
        if defined VC_MISSING (
            echo The Microsoft Visual C++ files that should be inside the pyruntime folder are missing.
            echo Re-extract the whole zip, or install https://aka.ms/vs/17/release/vc_redist.x64.exe
            echo and run start.bat again.
            echo.
        )
        echo Run start.bat again to retry the install. If this keeps failing, send the
        echo files in the logs folder along with a request for a new zip.
        del "%CHECK_OUT%" >nul 2>nul
        pause
        exit /b 1
    )
    del "%CHECK_OUT%" >nul 2>nul
    type nul > "%DEPS_MARKER%"
    call :log "components installed and verified"
)
echo.

rem The packaged interpreter takes over the role the dev flow gives the venv's
rem python.exe below (server launch, model cache check).
set "VENV_PY=%PY_EXE%"

