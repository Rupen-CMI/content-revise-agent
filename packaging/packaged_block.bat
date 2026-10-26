rem ---------------------------------------------------------------
rem 1. Python ships inside the package (pyruntime). It ignores any
rem    Python on the machine and needs no admin rights.
rem ---------------------------------------------------------------
set "PY_DIR=%~dp0pyruntime"
set "PY_EXE=%PY_DIR%\python.exe"
if not exist "%PY_EXE%" (
    echo This package is incomplete: the "pyruntime" folder is missing.
    echo Re-extract the whole zip, keeping all files together, and try again.
    pause
    exit /b 1
)
echo Using the bundled Python: %PY_EXE%
echo.

rem ---------------------------------------------------------------
rem 2. First launch only: install the app's components once, directly
rem    into the bundled runtime (no venv). The marker file below makes
rem    every later launch skip straight past this in a few seconds.
rem ---------------------------------------------------------------
set "REQS=%~dp0backend\requirements.txt"
set "DEPS_MARKER=%PY_DIR%\.deps_done"
if not exist "%REQS%" (
    echo This package is incomplete: "backend\requirements.txt" is missing.
    echo Re-extract the whole zip, keeping all files together, and try again.
    pause
    exit /b 1
)
if exist "%DEPS_MARKER%" (
    echo Checking components: already installed, nothing to download.
) else (
    echo First-time setup: downloading and installing components...
    echo Needs internet. This can take a few minutes - progress shows below.
    echo.
    "%PY_EXE%" -m pip install --no-input --only-binary=:all: --retries 5 --timeout 60 --disable-pip-version-check -r "%REQS%"
    if errorlevel 1 (
        echo.
        echo Could not download the components. Please check your internet
        echo connection ^(or a firewall blocking the downloads^) and run this
        echo file again. Nothing was installed yet, so nothing is broken.
        pause
        exit /b 1
    )
    rem Verify what was downloaded before marking the runtime "complete",
    rem so a half-broken install never gets silently skipped later.
    "%PY_EXE%" -c "import fastapi, uvicorn, openai, httpx, trafilatura, bs4, docx, ddgs, dotenv, feedparser, sentence_transformers; print('components ok')" >nul 2>nul
    if errorlevel 1 (
        echo.
        echo The components were downloaded but did not load correctly.
        echo Delete the file pyruntime\.deps_done and run start.bat again to
        echo retry the install. If this keeps failing, ask for a new zip.
        pause
        exit /b 1
    )
    type nul > "%DEPS_MARKER%"
)
echo.

rem The packaged interpreter takes over the role the dev flow gives the venv's
rem python.exe below (server launch, model cache check).
set "VENV_PY=%PY_EXE%"

