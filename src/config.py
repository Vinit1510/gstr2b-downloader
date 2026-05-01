"""Central configuration for the GSTR-2B Downloader."""
from __future__ import annotations

import sys
from pathlib import Path


def app_root() -> Path:
    """Return the directory where the running .exe (or main.py) lives.

    When packaged with PyInstaller (--onefile), sys.executable points to the
    .exe in the user's chosen install folder. In dev mode, we anchor to the
    project root so all data folders are created next to the source tree.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


APP_NAME = "GSTR-2B Downloader"
APP_VERSION = "1.0.0"

ROOT_DIR = app_root()
DATA_DIR = ROOT_DIR / "data"
DOWNLOADS_DIR = ROOT_DIR / "GSTR-2B"
REPORTS_DIR = ROOT_DIR / "Reports"
LOGS_DIR = ROOT_DIR / "logs"
SCREENSHOTS_DIR = LOGS_DIR / "screenshots"
SAMPLE_EXCEL = ROOT_DIR / "sample_clients.xlsx"
VAULT_FILE = DATA_DIR / "vault.dat"

# GST portal endpoints
GST_LOGIN_URL = "https://services.gst.gov.in/services/login"
GST_DASHBOARD_URL = "https://services.gst.gov.in/services/auth/dashboard"
GST_RETURNS_DASHBOARD_URL = (
    "https://return.gst.gov.in/returns/auth/dashboard"
)

# Polite-automation timing (seconds)
HUMAN_DELAY_MIN = 0.1
HUMAN_DELAY_MAX = 0.3
PAGE_LOAD_TIMEOUT_MS = 60_000
ELEMENT_TIMEOUT_MS = 20_000

# CAPTCHA
CAPTCHA_OCR_RETRIES = 3
CAPTCHA_LENGTH = 6  # GST portal CAPTCHA is exactly 6 alphanumeric chars

# Months for the dropdown (in order)
MONTHS = [
    "April", "May", "June", "July", "August", "September",
    "October", "November", "December", "January", "February", "March",
]
MONTH_NUMBER = {name: idx for idx, name in enumerate(
    ["January", "February", "March", "April", "May", "June",
     "July", "August", "September", "October", "November", "December"], start=1)}

def ensure_dirs() -> None:
    """Create all data folders if missing."""
    for d in (DATA_DIR, DOWNLOADS_DIR, REPORTS_DIR, LOGS_DIR, SCREENSHOTS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def fy_string_for(year: int, month: int) -> str:
    """Indian financial year string for a given month/year, e.g. 2025-26."""
    if month >= 4:
        start = year
    else:
        start = year - 1
    return f"{start}-{str(start + 1)[-2:]}"


def month_label(year: int, month: int) -> str:
    """Folder label like 'Apr-2025'."""
    months_short = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return f"{months_short[month - 1]}-{year}"
