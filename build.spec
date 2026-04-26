# PyInstaller spec for GSTR-2B Downloader
# Build with:  pyinstaller build.spec --clean --noconfirm
#
# Bundles Playwright's Chromium browser inside the .exe so the end-user
# does NOT need to run `playwright install` separately.
# -*- mode: python ; coding: utf-8 -*-

import os
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, collect_data_files, collect_all

block_cipher = None

# ---------------------------------------------------------------------------
# Pull in setuptools / pkg_resources internals that PyInstaller misses by
# default (jaraco.text, jaraco.context, backports.tarfile, etc.).
# ---------------------------------------------------------------------------
_pkgres_datas, _pkgres_binaries, _pkgres_hiddenimports = collect_all("pkg_resources")
_setup_datas, _setup_binaries, _setup_hiddenimports = collect_all("setuptools")

# ---------------------------------------------------------------------------
# Force-collect every module under our own `src` package so the frozen .exe
# can resolve `from src.xxx import yyy`.  Without this PyInstaller misses
# `src` because it is found via path rather than installed as a package.
# ---------------------------------------------------------------------------
_src_hiddenimports = collect_submodules("src")

# ---------------------------------------------------------------------------
# Resolve Playwright browser path so we can bundle it.
# ---------------------------------------------------------------------------
def _playwright_browsers_path() -> str:
    import playwright
    pkg_dir = Path(playwright.__file__).parent
    candidates = [
        Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")) if os.environ.get("PLAYWRIGHT_BROWSERS_PATH") else None,
        Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright",
        Path.home() / "AppData" / "Local" / "ms-playwright",
        Path.home() / ".cache" / "ms-playwright",
    ]
    for cand in candidates:
        if cand and cand.exists():
            return str(cand)
    raise FileNotFoundError(
        "Could not locate ms-playwright browsers. Run "
        "`python -m playwright install chromium` before building."
    )


browsers_path = _playwright_browsers_path()

datas = []

# Bundle the entire ms-playwright folder under _internal/ms-playwright
for entry in Path(browsers_path).iterdir():
    datas.append((str(entry), f"ms-playwright/{entry.name}"))

hiddenimports = [
    "playwright",
    "playwright.sync_api",
    "easyocr",
    "customtkinter",
    "openpyxl",
    "cryptography",
    "PIL",
    "PIL.Image",
    "cv2",
    "numpy",
    # setuptools / pkg_resources tail dependencies (PyInstaller misses these)
    "pkg_resources",
    "pkg_resources.extern",
    "pkg_resources._vendor",
    "pkg_resources._vendor.jaraco",
    "pkg_resources._vendor.jaraco.text",
    "pkg_resources._vendor.jaraco.context",
    "pkg_resources._vendor.jaraco.functools",
    "pkg_resources._vendor.backports",
    "pkg_resources._vendor.backports.tarfile",
    "jaraco",
    "jaraco.text",
    "jaraco.context",
    "jaraco.functools",
    "backports",
    "backports.tarfile",
    # Our own application package
    "src",
    "src.main",
    "src.config",
    "src.logger",
    "src.crypto_utils",
    "src.excel_io",
    "src.captcha_solver",
    "src.gst_portal",
    "src.orchestrator",
    "src.gui",
    "src.gui.main_window",
    "src.gui.master_password",
    "src.gui.captcha_dialog",
] + _pkgres_hiddenimports + _setup_hiddenimports + _src_hiddenimports

a = Analysis(
    ["run.py"],
    pathex=[str(Path(SPECPATH))],
    binaries=_pkgres_binaries + _setup_binaries,
    datas=datas + _pkgres_datas + _setup_datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=["runtime_hook.py"],
    excludes=[
        # Trim very heavy modules we never use to keep the .exe smaller.
        # NOTE: Do NOT exclude torch.testing or torch.distributions here --
        # EasyOCR imports torch.testing during initialisation; excluding it
        # breaks CAPTCHA auto-solve at runtime ("No module named 'torch.testing'").
        "matplotlib",
        "scipy",
        "tkinter.test",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="GSTR2B_Downloader",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join("assets", "icon.ico") if os.path.exists(os.path.join("assets", "icon.ico")) else None,
)
