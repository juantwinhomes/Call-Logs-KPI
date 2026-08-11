@echo off
REM ===========================================================================
REM  Build TwinCallTracker.exe
REM
REM  Usage, from a Command Prompt in this folder:
REM
REM      build.bat              one-folder build  (recommended)
REM      build.bat onefile      single .exe
REM      build.bat clean        delete build output and start fresh
REM
REM  Requires Python 3.10+ on PATH. Everything else is installed for you into a
REM  local virtual environment named .venv.
REM ===========================================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0"

set APP=TwinCallTracker
set VENV=.venv
set PY=%VENV%\Scripts\python.exe

if /I "%~1"=="clean" goto :clean

echo.
echo ============================================================
echo  Building %APP%.exe
echo ============================================================
echo.

REM ---------------------------------------------------------------- Python ---
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python was not found on PATH.
    echo         Install Python 3.11 from https://www.python.org/downloads/windows/
    echo         and tick "Add python.exe to PATH" during setup.
    goto :fail
)

for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo  Python found: %PYVER%

REM ------------------------------------------------------- virtual environment
if not exist "%PY%" (
    echo  Creating the virtual environment in %VENV% ...
    python -m venv "%VENV%"
    if errorlevel 1 (
        echo [ERROR] Could not create the virtual environment.
        goto :fail
    )
)

echo  Installing dependencies ...
"%PY%" -m pip install --upgrade pip --quiet --disable-pip-version-check
if errorlevel 1 goto :pipfail
"%PY%" -m pip install -r requirements.txt --quiet --disable-pip-version-check
if errorlevel 1 goto :pipfail

REM ------------------------------------------------------------------- tests --
echo.
echo  Running the tests ...
"%PY%" -m pytest tests -q
if errorlevel 1 (
    echo.
    echo [WARNING] Some tests did not pass. The build will continue, but please
    echo           look at the output above before handing this build to anyone.
    echo.
)

REM ------------------------------------------------------------------- build --
echo.
echo  Removing previous build output ...
if exist build rmdir /s /q build
if exist "dist\%APP%" rmdir /s /q "dist\%APP%"
if exist "dist\%APP%.exe" del /q "dist\%APP%.exe"

if /I "%~1"=="onefile" (
    echo  Packaging as a single file ...
    set BUILD_ONEFILE=1
) else (
    echo  Packaging as a folder ...
    set BUILD_ONEFILE=
)

"%PY%" -m PyInstaller %APP%.spec --noconfirm --clean
if errorlevel 1 (
    echo.
    echo [ERROR] PyInstaller failed. The output above says why.
    goto :fail
)

REM ------------------------------------------------------------------ result --
echo.
if /I "%~1"=="onefile" (
    if exist "dist\%APP%.exe" (
        echo ============================================================
        echo  Build finished
        echo ============================================================
        echo  dist\%APP%.exe
        for %%F in ("dist\%APP%.exe") do echo  Size: %%~zF bytes
        goto :done
    )
) else (
    if exist "dist\%APP%\%APP%.exe" (
        echo ============================================================
        echo  Build finished
        echo ============================================================
        echo  dist\%APP%\%APP%.exe
        echo.
        echo  Give the whole "dist\%APP%" folder to the people who will use it -
        echo  the .exe needs the files beside it.
        goto :done
    )
)
echo [ERROR] The build reported success but no executable was produced.
goto :fail

:done
echo.
echo  Before first use on a new machine, put client_secrets.json in:
echo     %%LOCALAPPDATA%%\%APP%\
echo  See README.md, section "Google setup".
echo.
goto :end

:clean
echo  Cleaning build output ...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist __pycache__ rmdir /s /q __pycache__
for /d /r %%d in (__pycache__) do @if exist "%%d" rmdir /s /q "%%d"
if exist .pytest_cache rmdir /s /q .pytest_cache
echo  Done. The virtual environment in %VENV% was left in place;
echo  delete that folder by hand if you want a completely fresh start.
goto :end

:pipfail
echo.
echo [ERROR] Installing dependencies failed.
echo         If this office is behind a proxy, try:
echo            set HTTPS_PROXY=http://your.proxy:8080
echo         and run this script again.
goto :fail

:fail
echo.
echo  BUILD FAILED
endlocal
exit /b 1

:end
endlocal
exit /b 0
