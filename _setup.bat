@echo off
setlocal
set "PY=C:\Users\MR Nkutha\AppData\Local\Programs\Python\Python314\python.exe"
cd /d "C:\Users\MR Nkutha\Documents\Default Project"

echo === python ===
"%PY%" --version

echo === recreating venv ===
if exist .venv rmdir /s /q .venv
"%PY%" -m venv .venv
if errorlevel 1 goto :fail

echo === installing requirements ===
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --upgrade pip
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto :fail

echo === installed ===
".venv\Scripts\python.exe" -m pip list --disable-pip-version-check
exit /b 0

:fail
echo.
echo *** SETUP FAILED (exit code %errorlevel%) ***
exit /b 1
