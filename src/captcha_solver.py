"""CAPTCHA OCR for GST portal using ddddocr with Multi-Pass Logic.

This module implements a robust captcha solver that uses multiple preprocessing
strategies and picks the best result.
"""
from __future__ import annotations

import logging
import re
import cv2
import numpy as np
from typing import Optional

log = logging.getLogger("gstr2b.captcha")

# The captcha can contain letters and numbers! (e.g. 9S7B73)
# But GST portal usually uses 6 digits.
_VALID = re.compile(r"^[0-9]{6}$")
_ocr_beta = None
_ocr_std = None

def _get_ocr_beta():
    global _ocr_beta
    if _ocr_beta is None:
        import ddddocr  # type: ignore
        log.info("Initialising ddddocr reader (BETA MODEL)...")
        _ocr_beta = ddddocr.DdddOcr(show_ad=False, beta=True)
        log.info("ddddocr BETA ready.")
    return _ocr_beta

def _get_ocr_std():
    global _ocr_std
    if _ocr_std is None:
        import ddddocr  # type: ignore
        log.info("Initialising ddddocr reader (STANDARD MODEL - Numbers Only)...")
        _ocr_std = ddddocr.DdddOcr(show_ad=False, beta=False)
        _ocr_std.set_ranges(0)  # Number only style
        log.info("ddddocr STANDARD ready.")
    return _ocr_std

def _preprocess_v8(img):
    """V8: CLAHE + Red to Black + NLM Denoise."""
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
    l = clahe.apply(l)
    img = cv2.merge((l, a, b))
    img = cv2.cvtColor(img, cv2.COLOR_LAB2BGR)
    
    denoised = cv2.fastNlMeansDenoisingColored(img, None, 10, 10, 7, 21)
    hsv = cv2.cvtColor(denoised, cv2.COLOR_BGR2HSV)
    _, s, _ = cv2.split(hsv)
    _, mask = cv2.threshold(s, 70, 255, cv2.THRESH_BINARY_INV)
    gray = cv2.cvtColor(denoised, cv2.COLOR_BGR2GRAY)
    masked = cv2.bitwise_and(gray, gray, mask=mask)
    _, final = cv2.threshold(masked, 80, 255, cv2.THRESH_BINARY)
    return cv2.imencode('.png', final)[1].tobytes()

def _preprocess_v15(img):
    """V15: Pure CLAHE."""
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
    l = clahe.apply(l)
    img = cv2.merge((l, a, b))
    img = cv2.cvtColor(img, cv2.COLOR_LAB2BGR)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return cv2.imencode('.png', gray)[1].tobytes()

def _preprocess_v19(img):
    """V19: 2x Scale + V8 Logic."""
    img = cv2.resize(img, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    return _preprocess_v8(img)

def solve_captcha(image_bytes: bytes) -> Optional[str]:
    """Multi-pass solve using Beta and Standard models with multiple preprocessors."""
    try:
        ocr_beta = _get_ocr_beta()
        ocr_std = _get_ocr_std()
    except Exception as exc:
        log.error("ddddocr unavailable: %s", exc)
        return None

    img_array = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
    if img is None:
        return None

    results = []
    
    # Pre-calculate versions
    v8_bytes = _preprocess_v8(img)
    v15_bytes = _preprocess_v15(img)
    v19_bytes = _preprocess_v19(img)

    # Strategy 1: BETA Model - Multiple passes
    for b in [v19_bytes, v8_bytes, image_bytes, v15_bytes]:
        res = "".join(re.findall(r"\d", ocr_beta.classification(b)))
        if len(res) == 6: return res
        results.append(res)
    
    # Strategy 2: STANDARD Model (Number Style) - Multiple passes
    # Standard model is sometimes better for thin digits like 1 and 7
    for b in [v19_bytes, image_bytes, v8_bytes]:
        res = "".join(re.findall(r"\d", ocr_std.classification(b)))
        if len(res) == 6: return res
        results.append(res)

    # If no strategy gave 6 digits, return the longest one
    best = max(results, key=len)
    if len(best) >= 4:
        log.warning("Could not find perfect 6-digit match, using best guess: %s", best)
        return best

    return None
