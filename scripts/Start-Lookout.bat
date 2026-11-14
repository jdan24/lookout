@echo off
rem ---------------------------------------------------------------------------
rem  Start-Lookout.bat - starts Lookout on this PC and opens it in the browser.
rem  No admin rights needed. Lookout is read-only: it never changes TT.
rem ---------------------------------------------------------------------------
setlocal

rem Where the released copy of Lookout lives. EDIT THIS when the share is decided.
set "RELEASE_SHARE=\\CHANGE-ME\Lookout\current"

set "APP_DIR=%LOCALAPPDATA%\Lookout\app"
set "PORT=8501"

rem Use the Windows certificate store, so downloads work behind SSL inspection.
set "UV_NATIVE_TLS=1"

where uv >nul 2>nul
if errorlevel 1 (
    echo.
    echo  Lookout needs a small tool called "uv", which is not installed yet.
    echo  It installs for your user only and does not need admin rights.
    echo.
    echo  1. Open PowerShell
    echo  2. Paste this line and press Enter:
    rem The pipe below is inside quotes, so cmd prints it as is: no caret escape needed.
    echo.
    echo     powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    echo.
    echo  3. Close this window and start Lookout again.
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

echo Starting Lookout at http://localhost:%PORT% ...
echo Leave this window open while you use Lookout. Close it to stop Lookout.

rem Open the browser a few seconds after the server starts.
start "" /min cmd /c "timeout /t 5 /nobreak >nul & start http://localhost:%PORT%"

uv run --no-dev streamlit run app.py --server.address localhost --server.headless true --server.port %PORT% --browser.gatherUsageStats false

if errorlevel 1 (
    echo.
    echo  Lookout stopped with an error. The message above says why.
    echo.
    pause
)
endlocal
