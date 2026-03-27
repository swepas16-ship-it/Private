@echo off
setlocal
REM Build script for FluentPhotoStudio: creates venv (py 3.13/3.12), installs deps, builds onefile EXE.

REM Detect Python 3.13/3.12
for %%v in (3.13 3.12 3.11 3.10) do (
    py -%%v --version >nul 2>&1
    if not errorlevel 1 (
        set PYV=%%v
        goto :found
    )
)
echo [ERROR] Nie znaleziono interpretera Python 3.13/3.12/3.11/3.10. Zainstaluj jeden z nich i sprobuj ponownie.
echo [HINT] Pobierz installer z https://www.python.org/downloads/windows/ (64-bit) lub z Microsoft Store.
exit /b 1

:found
echo [INFO] Uzywam Python %PYV%
set VENV=.venv

REM Create venv if missing
if not exist "%VENV%\Scripts\python.exe" (
    py -%PYV% -m venv %VENV%
)

call "%VENV%\Scripts\activate.bat"

echo [INFO] Instalacja zaleznosci...
python -m pip install --upgrade pip >nul
python -m pip install PySide6==6.9.3 pyinstaller >nul
if errorlevel 1 (
    echo [ERROR] Instalacja zaleznosci nie powiodla sie.
    exit /b 1
)

echo [INFO] Budowanie EXE...
python -m PyInstaller --noconsole --onefile --name FluentPhotoStudio main.py
if errorlevel 1 (
    echo [ERROR] Build nie powiodl sie.
    exit /b 1
)

echo [DONE] Gotowe: dist\FluentPhotoStudio.exe
endlocal
