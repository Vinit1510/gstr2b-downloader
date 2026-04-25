"""GST portal automation via Playwright (sync API).

This module is the only place that should know about the portal's HTML.
If GSTN changes the page, only the constants and helpers here need updating.

The flow:
    1. open_login_page()
    2. fetch_captcha_image() -> bytes
    3. submit_credentials(user_id, password, captcha_text) -> bool
    4. navigate_to_returns_dashboard()
    5. download_gstr2b(year, month, save_dir, filename) -> Path

Each high-level method raises a typed exception on failure so the
orchestrator can map it to a user-friendly status.
"""
from __future__ import annotations

import logging
import random
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

from playwright.sync_api import (
    BrowserContext,
    Page,
    Playwright,
    TimeoutError as PWTimeout,
    sync_playwright,
)

from . import config

log = logging.getLogger("gstr2b.portal")


# ---------------------------------------------------------------------------
# Selectors. Keep them grouped here so they're easy to update.
# Multiple fallbacks per element so a small portal change doesn't break us.
# ---------------------------------------------------------------------------

SEL_USERNAME = ["#username", "input[name='username']"]
SEL_PASSWORD = ["#user_pass", "input[name='user_pass']"]
SEL_CAPTCHA_IMAGE = ["#imgCaptcha", "img.captcha-image", "img[alt*='captcha' i]"]
SEL_CAPTCHA_INPUT = ["#captcha", "input[name='captcha']"]
SEL_LOGIN_BUTTON = [
    "button[type='submit']:has-text('LOGIN')",
    "button:has-text('LOGIN')",
    "input[type='submit'][value='LOGIN' i]",
]
SEL_LOGIN_ERROR = [
    ".alert-danger",
    ".error-msg",
    "#errorMsg",
    "div:has-text('Invalid Username or Password')",
]
SEL_DASHBOARD_MARKER = [
    "a:has-text('Return Dashboard')",
    "a:has-text('Returns Dashboard')",
    "text=Welcome",
]

# Returns dashboard
SEL_RETURNS_DASHBOARD_LINK = [
    "a:has-text('Return Dashboard')",
    "a:has-text('Returns Dashboard')",
]
SEL_FY_DROPDOWN = ["#fin", "select[name='fin']"]
SEL_QUARTER_DROPDOWN = ["#quarter", "select[name='quarter']"]
SEL_MONTH_DROPDOWN = ["#mon", "select[name='mon']"]
SEL_SEARCH_BUTTON = [
    "button:has-text('SEARCH')",
    "input[type='submit'][value='SEARCH' i]",
]

# GSTR-2B tile + download
SEL_GSTR2B_DOWNLOAD = [
    "button:has-text('DOWNLOAD'):below(:text('GSTR-2B'))",
    "a:has-text('DOWNLOAD'):below(:text('GSTR-2B'))",
    "div:has-text('GSTR-2B') >> button:has-text('DOWNLOAD')",
]
SEL_GENERATE_EXCEL = [
    "button:has-text('GENERATE EXCEL FILE TO DOWNLOAD')",
    "button:has-text('GENERATE EXCEL')",
    "a:has-text('GENERATE EXCEL FILE TO DOWNLOAD')",
]
SEL_DOWNLOAD_EXCEL_READY = [
    "button:has-text('DOWNLOAD EXCEL FILE')",
    "a:has-text('DOWNLOAD EXCEL FILE')",
    "button:has-text('DOWNLOAD')",
]


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class PortalError(Exception):
    """Base for all portal failures the orchestrator should map to a status."""


class WrongPasswordError(PortalError):
    pass


class CaptchaFailedError(PortalError):
    pass


class LoginFailedError(PortalError):
    pass


class NavigationError(PortalError):
    pass


class DownloadError(PortalError):
    pass


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


@dataclass
class _SelectorHit:
    selector: str
    locator: any  # playwright Locator


def _human_pause():
    time.sleep(random.uniform(config.HUMAN_DELAY_MIN, config.HUMAN_DELAY_MAX))


def _first_visible(page: Page, selectors: list[str], timeout_ms: int) -> _SelectorHit:
    """Return the first selector whose locator becomes visible."""
    deadline = time.monotonic() + timeout_ms / 1000.0
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if loc.is_visible(timeout=500):
                    return _SelectorHit(selector=sel, locator=loc)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
        time.sleep(0.25)
    raise PWTimeout(
        f"None of the selectors became visible: {selectors}. Last error: {last_error}"
    )


class GstSession:
    """One browser context per client. Use as a context manager."""

    def __init__(
        self,
        playwright: Playwright,
        download_dir: Path,
        headless: bool = True,
    ) -> None:
        self._pw = playwright
        self._download_dir = download_dir
        self._headless = headless
        self._browser = None
        self._context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

    def __enter__(self) -> "GstSession":
        self._browser = self._pw.chromium.launch(headless=self._headless)
        self._context = self._browser.new_context(
            accept_downloads=True,
            viewport={"width": 1366, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            ),
        )
        self._context.set_default_timeout(config.ELEMENT_TIMEOUT_MS)
        self.page = self._context.new_page()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if self._context:
                self._context.close()
        finally:
            if self._browser:
                self._browser.close()

    # ------------------ login flow ------------------

    def open_login_page(self) -> None:
        assert self.page is not None
        log.info("Opening GST login page...")
        self.page.goto(config.GST_LOGIN_URL, timeout=config.PAGE_LOAD_TIMEOUT_MS)
        # Wait for the login form
        _first_visible(self.page, SEL_USERNAME, config.ELEMENT_TIMEOUT_MS)

    def fetch_captcha_image(self) -> bytes:
        """Return the current CAPTCHA image bytes (PNG)."""
        assert self.page is not None
        hit = _first_visible(self.page, SEL_CAPTCHA_IMAGE, config.ELEMENT_TIMEOUT_MS)
        # screenshot of the element gives us the rendered pixels
        return hit.locator.screenshot()

    def refresh_captcha(self) -> None:
        """Click the CAPTCHA image to refresh it (GST portal supports this)."""
        assert self.page is not None
        try:
            hit = _first_visible(self.page, SEL_CAPTCHA_IMAGE, 5000)
            hit.locator.click()
            time.sleep(1)
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not refresh CAPTCHA: %s", exc)

    def submit_credentials(self, user_id: str, password: str, captcha_text: str) -> None:
        """Fill the form and click LOGIN. Raises on known failures."""
        assert self.page is not None
        page = self.page

        u_hit = _first_visible(page, SEL_USERNAME, config.ELEMENT_TIMEOUT_MS)
        u_hit.locator.fill(user_id)
        _human_pause()

        p_hit = _first_visible(page, SEL_PASSWORD, config.ELEMENT_TIMEOUT_MS)
        p_hit.locator.fill(password)
        _human_pause()

        c_hit = _first_visible(page, SEL_CAPTCHA_INPUT, config.ELEMENT_TIMEOUT_MS)
        c_hit.locator.fill(captcha_text)
        _human_pause()

        btn = _first_visible(page, SEL_LOGIN_BUTTON, config.ELEMENT_TIMEOUT_MS)
        btn.locator.click()

        # Wait for either dashboard OR a known error
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if any(self._is_visible(sel) for sel in SEL_DASHBOARD_MARKER):
                log.info("Login successful.")
                return
            err_text = self._read_first_visible_text(SEL_LOGIN_ERROR)
            if err_text:
                err_lc = err_text.lower()
                log.warning("Login error message: %s", err_text)
                if "captcha" in err_lc:
                    raise CaptchaFailedError(err_text)
                if "username" in err_lc and "password" in err_lc:
                    raise WrongPasswordError(err_text)
                if "invalid" in err_lc or "incorrect" in err_lc:
                    raise WrongPasswordError(err_text)
                raise LoginFailedError(err_text)
            time.sleep(0.5)

        raise LoginFailedError(
            "Login did not complete in time (no dashboard, no error message)."
        )

    # ------------------ navigate to GSTR-2B ------------------

    def navigate_to_returns_dashboard(self) -> None:
        assert self.page is not None
        log.info("Navigating to Returns Dashboard...")
        try:
            hit = _first_visible(self.page, SEL_RETURNS_DASHBOARD_LINK, 8000)
            hit.locator.click()
        except PWTimeout:
            # Fallback: direct URL
            self.page.goto(config.GST_RETURNS_DASHBOARD_URL,
                           timeout=config.PAGE_LOAD_TIMEOUT_MS)
        _first_visible(self.page, SEL_FY_DROPDOWN, config.ELEMENT_TIMEOUT_MS)

    def select_period(self, year: int, month: int) -> None:
        """Select FY + month, then click SEARCH."""
        assert self.page is not None
        page = self.page

        fy = config.fy_string_for(year, month)
        log.info("Selecting period FY=%s month=%d/%d", fy, month, year)

        fy_hit = _first_visible(page, SEL_FY_DROPDOWN, config.ELEMENT_TIMEOUT_MS)
        fy_hit.locator.select_option(label=fy)
        _human_pause()

        # Some pages have Quarter dropdown — pick the quarter that contains month.
        try:
            q_hit = _first_visible(page, SEL_QUARTER_DROPDOWN, 3000)
            quarter_label = _quarter_label(month)
            q_hit.locator.select_option(label=quarter_label)
            _human_pause()
        except PWTimeout:
            pass  # no quarter dropdown on this layout

        m_hit = _first_visible(page, SEL_MONTH_DROPDOWN, config.ELEMENT_TIMEOUT_MS)
        month_label = _full_month_label(month)
        m_hit.locator.select_option(label=month_label)
        _human_pause()

        search = _first_visible(page, SEL_SEARCH_BUTTON, config.ELEMENT_TIMEOUT_MS)
        search.locator.click()
        time.sleep(2)

    def download_gstr2b(self, save_path: Path) -> Path:
        """Click DOWNLOAD on the GSTR-2B tile, then DOWNLOAD EXCEL.

        Saves the file to `save_path` (parent dirs must exist).
        Returns the final path.
        """
        assert self.page is not None
        page = self.page
        log.info("Initiating GSTR-2B download...")

        # Step 1: GSTR-2B tile DOWNLOAD button
        try:
            tile = _first_visible(page, SEL_GSTR2B_DOWNLOAD,
                                  config.ELEMENT_TIMEOUT_MS)
            tile.locator.click()
        except PWTimeout as exc:
            raise NavigationError(
                "GSTR-2B DOWNLOAD button not found on returns dashboard."
            ) from exc

        # Step 2: GENERATE EXCEL FILE TO DOWNLOAD
        try:
            gen = _first_visible(page, SEL_GENERATE_EXCEL,
                                 config.ELEMENT_TIMEOUT_MS)
            gen.locator.click()
        except PWTimeout as exc:
            raise NavigationError(
                "'Generate Excel' button not found on GSTR-2B page."
            ) from exc

        # The portal shows a "request being processed" message and after a
        # few seconds the actual download button becomes enabled.
        log.info("Waiting for portal to prepare the file (up to 90s)...")
        deadline = time.monotonic() + 90
        download_btn = None
        while time.monotonic() < deadline:
            try:
                download_btn = _first_visible(page, SEL_DOWNLOAD_EXCEL_READY, 2000)
                break
            except PWTimeout:
                pass
        if download_btn is None:
            raise DownloadError(
                "Portal did not produce a downloadable file in time."
            )

        # Step 3: click and capture the download
        save_path.parent.mkdir(parents=True, exist_ok=True)
        with page.expect_download(timeout=120_000) as dl_info:
            download_btn.locator.click()
        download = dl_info.value
        download.save_as(str(save_path))
        log.info("Saved: %s", save_path)
        return save_path

    def logout(self) -> None:
        """Best-effort logout to release the GST session quickly."""
        assert self.page is not None
        try:
            self.page.locator("a:has-text('Logout'), a:has-text('Sign Out')").first.click(
                timeout=3000
            )
        except Exception:  # noqa: BLE001
            pass

    # ------------------ small helpers ------------------

    def _is_visible(self, selector: str) -> bool:
        assert self.page is not None
        try:
            return self.page.locator(selector).first.is_visible(timeout=300)
        except Exception:
            return False

    def _read_first_visible_text(self, selectors: list[str]) -> str:
        assert self.page is not None
        for sel in selectors:
            try:
                loc = self.page.locator(sel).first
                if loc.is_visible(timeout=300):
                    return (loc.inner_text(timeout=1000) or "").strip()
            except Exception:
                continue
        return ""


# ---------------------------------------------------------------------------
# Module-level Playwright lifecycle helper
# ---------------------------------------------------------------------------


@contextmanager
def playwright_session() -> Iterator[Playwright]:
    pw = sync_playwright().start()
    try:
        yield pw
    finally:
        pw.stop()


# ---------------------------------------------------------------------------
# Period helpers (portal-specific labels)
# ---------------------------------------------------------------------------


def _full_month_label(month: int) -> str:
    return [
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    ][month - 1]


def _quarter_label(month: int) -> str:
    if month in (4, 5, 6):
        return "Apr - Jun"
    if month in (7, 8, 9):
        return "Jul - Sep"
    if month in (10, 11, 12):
        return "Oct - Dec"
    return "Jan - Mar"
