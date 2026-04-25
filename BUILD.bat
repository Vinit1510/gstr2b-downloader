@echo off
REM ============================================================
REM GSTR-2B Downloader — local Windows build (Option B fallback)
REM
REM Prerequisite: Python 3.11 64-bit installed and on PATH.
REM Result: dist\GSTR2B_Downloader.exe
REM ============================================================

setlocal
echo.
echo === [1/5] Creating virtual environment ===
if not exist .venv (
    python -m venv .venv || goto :fail
)
call .venv\Scripts\activate || goto :fail

echo.
echo === [2/5] Installing dependencies (this can take 5-10 min the first time) ===
python -m pip install --upgrade pip || goto :fail
pip install -r requirements.txt || goto :fail
pip install pyinstaller==6.11.0 || goto :fail

echo.
echo === [3/5] Downloading bundled Chromium for Playwright ===
python -m playwright install chromium || goto :fail

echo.
echo === [4/5] Pre-downloading EasyOCR English model ===
python -c "import easyocr; easyocr.Reader(['en'], gpu=False, verbose=False)" || goto :fail

echo.
echo === [5/5] Building single-file Windows .exe ===
pyinstaller build.spec --clean --noconfirm || goto :fail

echo.
echo ====================================================
echo  SUCCESS
echo  Your .exe is here:  dist\GSTR2B_Downloader.exe
echo ====================================================
echo.
pause
exit /b 0

:fail
echo.
echo *** Build failed. Scroll up to see the error. ***
pause
exit /b 1
