"""CAPTCHA OCR for GST portal.

The GST portal CAPTCHA is exactly 6 alphanumeric characters on a noisy
white-ish background. We try EasyOCR with several preprocessing variants
and pick the first result that looks like a valid 6-char alnum string.

EasyOCR is loaded lazily because it is heavy (~300 MB at runtime).
"""
from __future__ import annotations

import io
import logging
import re
from typing import Optional

import cv2
import numpy as np
from PIL import Image

log = logging.getLogger("gstr2b.captcha")

_VALID = re.compile(r"^[A-Za-z0-9]{6}$")
_reader = None  # lazy easyocr.Reader


def _get_reader():
    global _reader
    if _reader is None:
        # Import here so app can start without easyocr (e.g. dev without GPU/CPU torch)
        import easyocr  # type: ignore
        log.info("Initialising EasyOCR reader (first call may take ~10s)...")
        _reader = easyocr.Reader(["en"], gpu=False, verbose=False)
        log.info("EasyOCR ready.")
    return _reader


def _preprocess_variants(image_bytes: bytes) -> list[np.ndarray]:
    """Return multiple cleaned-up versions of the CAPTCHA to OCR."""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    arr = np.array(img)

    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    gray = cv2.resize(gray, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)

    variants = []

    # 1. Plain Otsu binarisation
    _, v1 = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    variants.append(v1)

    # 2. Adaptive threshold + light open
    v2 = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 25, 10
    )
    kernel = np.ones((2, 2), np.uint8)
    v2 = cv2.morphologyEx(v2, cv2.MORPH_OPEN, kernel)
    variants.append(v2)

    # 3. Inverted binary (some CAPTCHA renderers prefer this)
    _, v3 = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    variants.append(v3)

    # 4. Slight blur + threshold (kills speckle noise)
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    _, v4 = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    variants.append(v4)

    return variants


def _clean(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", text or "")


def solve_captcha(image_bytes: bytes) -> Optional[str]:
    """Best-effort solve. Returns the 6-char string or None."""
    try:
        reader = _get_reader()
    except Exception as exc:  # noqa: BLE001
        log.error("EasyOCR unavailable: %s", exc)
        return None

    candidates: list[str] = []
    for variant in _preprocess_variants(image_bytes):
        try:
            results = reader.readtext(
                variant, detail=0, paragraph=False, allowlist=
                "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789",
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("OCR pass failed: %s", exc)
            continue

        joined = _clean("".join(results))
        if joined:
            candidates.append(joined)

    log.debug("CAPTCHA candidates: %s", candidates)
    for cand in candidates:
        if _VALID.match(cand):
            return cand

    # If any candidate is at least 6 chars, take the first 6 (often the
    # last char is misread as extra). Tunable.
    for cand in candidates:
        if len(cand) >= 6:
            return cand[:6]

    return None
