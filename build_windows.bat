@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 goto use_python
py -3 -c "import sys, tkinter; sys.exit(sys.version_info < (3, 11))" >nul 2>nul
if errorlevel 1 goto use_python
py -3 -m venv .venv-build
if errorlevel 1 goto failed
goto install
:use_python
where python >nul 2>nul
if errorlevel 1 goto missing_python
python -c "import sys, tkinter; sys.exit(sys.version_info < (3, 11))" >nul 2>nul
if errorlevel 1 goto missing_python
python -m venv .venv-build
if errorlevel 1 goto failed
:install
".venv-build\Scripts\python.exe" -m pip install -r requirements-error-band.txt pyinstaller
if errorlevel 1 goto failed
".venv-build\Scripts\python.exe" build_portable.py
if errorlevel 1 goto failed
echo Ready: dist\ErrorBandStudio-Windows.zip
echo Extract the whole ZIP and test ErrorBandStudio.exe with ExampleData before sharing.
pause
exit /b 0
:missing_python
echo Install Python 3.11 or newer with Tk support before building the Windows edition.
pause
exit /b 1
:failed
echo Build failed. Review the error above.
pause
exit /b 1
