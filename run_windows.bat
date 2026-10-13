@echo off
setlocal
cd /d "%~dp0"
if exist ".venv-band\Scripts\python.exe" goto check
where py >nul 2>nul
if errorlevel 1 goto use_python
py -3 -c "import sys, tkinter; sys.exit(sys.version_info < (3, 11))" >nul 2>nul
if errorlevel 1 goto use_python
py -3 -m venv .venv-band
if errorlevel 1 goto failed
goto install
:use_python
where python >nul 2>nul
if errorlevel 1 goto missing_python
python -c "import sys, tkinter; sys.exit(sys.version_info < (3, 11))" >nul 2>nul
if errorlevel 1 goto missing_python
python -m venv .venv-band
if errorlevel 1 goto failed
:install
".venv-band\Scripts\python.exe" -m pip install -r requirements-error-band.txt
if errorlevel 1 goto failed
".venv-band\Scripts\python.exe" -c "import numpy, matplotlib, openpyxl, tkinter"
if errorlevel 1 goto failed
goto run
:check
".venv-band\Scripts\python.exe" -c "import sys, tkinter; sys.exit(sys.version_info < (3, 11))" >nul 2>nul
if errorlevel 1 goto incompatible_environment
".venv-band\Scripts\python.exe" -c "import numpy, matplotlib, openpyxl, tkinter"
if errorlevel 1 goto install
:run
".venv-band\Scripts\python.exe" main.py
if errorlevel 1 goto failed
exit /b 0
:missing_python
echo Python 3.11 or newer with Tk support is required for the source edition.
echo Use the Windows native ZIP to run without installing Python.
pause
exit /b 1
:incompatible_environment
echo The existing .venv-band needs Python 3.11 or newer with Tk support.
echo Repair its Python installation or recreate this environment.
pause
exit /b 1
:failed
echo Startup failed. Review the error above and README_PORTABLE.md.
pause
exit /b 1
