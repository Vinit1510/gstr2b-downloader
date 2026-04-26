"""Bulk download orchestrator with resume + progress callbacks.

Public entry point: ``run_batch(...)``. The GUI thread calls this from a
background worker thread and reads queue updates to refresh its UI.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from . import config
from .captcha_solver import solve_captcha
from .excel_io import Client, ClientResult, write_report, report_filename
from .gst_portal import (
    CaptchaFailedError,
    DownloadError,
    GstSession,
    LoginFailedError,
    NavigationError,
    NoDataAvailableError,
    PortalError,
    WrongPasswordError,
    playwright_session,
)

log = logging.getLogger("gstr2b.orchestrator")


# A callback the orchestrator uses to ask the GUI for a manual CAPTCHA.
# Signature: callback(image_bytes, attempt_no) -> Optional[str]
# Returning None means "give up on this client".
ManualCaptchaCb = Callable[[bytes, int], Optional[str]]

# A callback to push live status updates back to the GUI.
# Signature: callback(result: ClientResult)
StatusUpdateCb = Callable[[ClientResult], None]


@dataclass
class BatchOptions:
    year: int
    month: int
    base_download_dir: Path
    headless: bool = True
    max_captcha_attempts: int = 3
    skip_existing: bool = True
    cancel_event: Optional[threading.Event] = None


def _client_target_path(opts: BatchOptions, client: Client) -> Path:
    fy = config.fy_string_for(opts.year, opts.month)
    month_lbl = config.month_label(opts.year, opts.month)
    folder = (
        opts.base_download_dir / fy / month_lbl / client.safe_folder_name()
    )
    filename = (
        f"{client.safe_folder_name()}_{month_lbl}_GSTR2B.xlsx"
    )
    return folder / filename


def _process_one(
    client: Client,
    opts: BatchOptions,
    pw,
    manual_captcha_cb: Optional[ManualCaptchaCb],
) -> ClientResult:
    result = ClientResult(client=client)
    result.started_at = datetime.now().strftime("%H:%M:%S")
    target_file = _client_target_path(opts, client)

    if opts.skip_existing and target_file.exists() and target_file.stat().st_size > 0:
        result.status = "Already Downloaded"
        result.file_path = str(target_file)
        result.finished_at = datetime.now().strftime("%H:%M:%S")
        log.info("[%s] Already downloaded -> skipping", client.name)
        return result

    log.info("[%s] starting", client.name)

    sess: Optional[GstSession] = None
    try:
        sess_cm = GstSession(
            pw,
            target_file.parent,
            headless=opts.headless,
            screenshot_dir=config.SCREENSHOTS_DIR,
            client_name=client.name,
        )
        with sess_cm as sess:
            try:
                sess.open_login_page()

                # Username must be entered BEFORE CAPTCHA loads
                sess.enter_username(client.user_id)

                # CAPTCHA + login with retries
                login_done = False
                last_error: Exception | None = None
                for attempt in range(1, opts.max_captcha_attempts + 1):
                    if opts.cancel_event and opts.cancel_event.is_set():
                        raise RuntimeError("Cancelled by user.")

                    img = sess.fetch_captcha_image()
                    solved = solve_captcha(img)

                    captcha_text: Optional[str] = solved
                    if not captcha_text and manual_captcha_cb:
                        log.info("[%s] OCR failed; asking user (attempt %d)",
                                 client.name, attempt)
                        captcha_text = manual_captcha_cb(img, attempt)
                        if not captcha_text:
                            raise CaptchaFailedError(
                                "User cancelled manual CAPTCHA entry."
                            )
                    elif not captcha_text:
                        log.warning("[%s] OCR failed and no manual fallback set",
                                    client.name)
                        sess.refresh_captcha()
                        continue

                    try:
                        sess.submit_login(client.password, captcha_text)
                        login_done = True
                        break
                    except CaptchaFailedError as exc:
                        last_error = exc
                        log.warning("[%s] Wrong CAPTCHA (attempt %d/%d): %s",
                                    client.name, attempt,
                                    opts.max_captcha_attempts, exc)
                        sess.refresh_captcha()
                        continue
                    except WrongPasswordError as exc:
                        raise exc

                if not login_done:
                    raise CaptchaFailedError(
                        f"CAPTCHA failed after {opts.max_captcha_attempts} attempts"
                        + (f" ({last_error})" if last_error else "")
                    )

                sess.navigate_to_returns_dashboard()
                sess.select_period(opts.year, opts.month)
                sess.open_gstr2b_view()
                saved = sess.download_gstr2b_excel(target_file)

                sess.logout()
                result.status = "Success"
                result.file_path = str(saved)

            except NoDataAvailableError:
                # normal/expected; no screenshot needed
                raise
            except WrongPasswordError:
                _safe_screenshot(sess, "wrong_password")
                raise
            except CaptchaFailedError:
                _safe_screenshot(sess, "captcha_failed")
                raise
            except LoginFailedError:
                _safe_screenshot(sess, "login_failed")
                raise
            except NavigationError:
                _safe_screenshot(sess, "nav_error")
                raise
            except DownloadError:
                _safe_screenshot(sess, "download_error")
                raise
            except PortalError:
                _safe_screenshot(sess, "portal_error")
                raise
            except Exception:
                _safe_screenshot(sess, "unexpected")
                raise

    except WrongPasswordError as exc:
        result.status = "Wrong Password"
        result.error_reason = str(exc)
        log.error("[%s] WRONG PASSWORD: %s", client.name, exc)
    except CaptchaFailedError as exc:
        result.status = "CAPTCHA Failed"
        result.error_reason = str(exc)
        log.error("[%s] CAPTCHA FAILED: %s", client.name, exc)
    except LoginFailedError as exc:
        result.status = "Failed Login"
        result.error_reason = str(exc)
        log.error("[%s] LOGIN FAILED: %s", client.name, exc)
    except NoDataAvailableError as exc:
        result.status = "No Data Available"
        result.error_reason = str(exc)
        log.info("[%s] NO DATA: %s", client.name, exc)
    except NavigationError as exc:
        result.status = "Portal Error"
        result.error_reason = f"Navigation: {exc}"
        log.error("[%s] NAV ERROR: %s", client.name, exc)
    except DownloadError as exc:
        result.status = "Portal Error"
        result.error_reason = f"Download: {exc}"
        log.error("[%s] DOWNLOAD ERROR: %s", client.name, exc)
    except PortalError as exc:
        result.status = "Portal Error"
        result.error_reason = str(exc)
        log.error("[%s] PORTAL ERROR: %s", client.name, exc)
    except Exception as exc:  # noqa: BLE001
        result.status = "Portal Error"
        result.error_reason = f"Unexpected: {exc}"
        log.exception("[%s] UNEXPECTED", client.name)

    result.finished_at = datetime.now().strftime("%H:%M:%S")
    return result


def _safe_screenshot(sess: Optional[GstSession], label: str) -> None:
    """Save a debug screenshot if we still have an open session."""
    if sess is None:
        return
    try:
        sess.take_screenshot(label)
    except Exception:  # noqa: BLE001
        pass


def run_batch(
    clients: list[Client],
    opts: BatchOptions,
    on_status: Optional[StatusUpdateCb] = None,
    manual_captcha: Optional[ManualCaptchaCb] = None,
) -> tuple[list[ClientResult], Path]:
    """Process every client in order. Returns (results, report_path)."""
    config.ensure_dirs()
    opts.base_download_dir.mkdir(parents=True, exist_ok=True)

    log.info("Batch starting: %d clients, %d/%d, headless=%s",
             len(clients), opts.month, opts.year, opts.headless)

    results: list[ClientResult] = []
    with playwright_session() as pw:
        for client in clients:
            if opts.cancel_event and opts.cancel_event.is_set():
                log.info("Batch cancelled by user.")
                break

            res = _process_one(client, opts, pw, manual_captcha)
            results.append(res)
            if on_status:
                try:
                    on_status(res)
                except Exception:  # noqa: BLE001
                    log.exception("status callback failed")

            # polite pause between clients
            time.sleep(2)

    # Write report
    report_path = config.REPORTS_DIR / report_filename(opts.year, opts.month)
    write_report(report_path, results)
    log.info("Report written: %s", report_path)
    return results, report_path
