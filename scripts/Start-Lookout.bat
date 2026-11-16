@echo off
rem ---------------------------------------------------------------------------
rem  Start-Lookout.bat - starts Lookout on this PC and opens it in the browser.
rem  No admin rights needed. Lookout is read-only: it never changes TT.
rem  It uses the Python already on this PC. It checks that the packages are
rem  there, but it never installs or downloads anything.
rem
rem  This starts LIVE (TT production). For UAT use Start-Lookout-UAT.bat, which
rem  calls this file with the word "uat".
rem ---------------------------------------------------------------------------
setlocal

rem Where the released copy of Lookout lives. EDIT THIS when the share is decided.
set "RELEASE_SHARE=\\CHANGE-ME\Lookout\current"

set "APP_DIR=%LOCALAPPDATA%\Lookout\app"

rem The mode is set here and nowhere else, so a session can never switch by itself.
set "LOOKOUT_ENVIRONMENT=ext_prod_live"
set "MODE=LIVE"
set "PORT=8501"
if /i "%~1"=="uat" (
    set "LOOKOUT_ENVIRONMENT=ext_uat_cert"
    set "MODE=UAT"
    set "PORT=8502"
)
title Lookout %MODE%

rem Run Python rather than look for it: Windows has a Store placeholder called
rem "python" that is found even when Python is not installed.
python -c "import sys" >nul 2>nul
if errorlevel 1 (
    echo.
    echo  Lookout needs Python, and typing "python" does not start it on this PC.
    echo  Check that Python is installed and on your PATH, then start Lookout again.
    echo.
    pause
    exit /b 1
)

if exist "%RELEASE_SHARE%\app.py" (
    echo Updating Lookout from %RELEASE_SHARE% ...
    robocopy "%RELEASE_SHARE%" "%APP_DIR%" /MIR /XD .venv .git snapshots __pycache__ .pytest_cache .ruff_cache /NFL /NDL /NJH /NJS /NP >nul
    if errorlevel 8 (
        echo.
        echo  Could not copy the latest version from the share.
        echo  Lookout will start with the copy already on this PC, if there is one.
        echo.
    )
) else (
    echo The release share is not reachable: %RELEASE_SHARE%
    echo Starting the copy already on this PC.
)

if not exist "%APP_DIR%\app.py" (
    echo.
    echo  Lookout is not installed on this PC yet and the share could not be reached.
    echo  Connect to the network and try again.
    echo.
    pause
    exit /b 1
)

cd /d "%APP_DIR%"

rem Lists any package that is missing or too old. It only looks; it installs nothing.
python scripts\check_environment.py
if errorlevel 1 (
    echo  Lookout will try to start anyway. If it fails, the list above is the likely reason.
    echo.
)

echo.
echo  Mode: %MODE%  (TT environment %LOOKOUT_ENVIRONMENT%)
echo.
echo Starting Lookout %MODE% at http://localhost:%PORT% ...
echo Leave this window open while you use Lookout. Close it to stop Lookout.

rem Open the browser a few seconds after the server starts.
start "" /min cmd /c "timeout /t 5 /nobreak >nul & start http://localhost:%PORT%"

python -m streamlit run app.py --server.address localhost --server.headless true --server.port %PORT% --browser.gatherUsageStats false

if errorlevel 1 (
    echo.
    echo  Lookout stopped with an error. The message above says why.
    echo.
    pause
)
endlocal
