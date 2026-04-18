import cv2
import numpy as np
import logging
import os
from contextlib import contextmanager
from pyzbar import pyzbar

logging.basicConfig(level=logging.INFO)

@contextmanager
def _suppress_zbar_stderr():
    """Suppress noisy C-level assertions from zbar (DataBar decoder false alarms)."""
    devnull_fd = os.open(os.devnull, os.O_WRONLY)
    old_stderr = os.dup(2)
    os.dup2(devnull_fd, 2)
    try:
        yield
    finally:
        os.dup2(old_stderr, 2)
        os.close(devnull_fd)
        os.close(old_stderr)

def try_decode(img):
    gray = img if len(img.shape) == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    bgr = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR) if len(img.shape) == 2 else img

    with _suppress_zbar_stderr():
        d = pyzbar.decode(bgr)
        if d:
            return d[0].data.decode("utf-8", errors="replace")
        d = pyzbar.decode(gray)
        if d:
            return d[0].data.decode("utf-8", errors="replace")

    return None

def _rotate(img, angle):
    h, w = img.shape[:2]
    M = cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
    return cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_REPLICATE)

def smart_decode(frame_bgr, x1, y1, x2, y2, padding=30):
    H, W = frame_bgr.shape[:2]
    crop = frame_bgr[max(0, y1 - padding):min(H, y2 + padding),
                     max(0, x1 - padding):min(W, x2 + padding)]

    if crop.size == 0 or min(crop.shape[:2]) < 10:
        return None, "invalid_crop"

    # 1. Direct decode at original resolution — fastest path, return immediately if success
    r = try_decode(crop)
    if r:
        return r, "direct"

    # 2. Multi-scale upscale (helps with small/blurry QR)
    for size in [400, 300, 500]:
        rs = cv2.resize(crop, (size, size), interpolation=cv2.INTER_CUBIC)
        r = try_decode(rs)
        if r:
            return r, f"resize_{size}"

    # Work on 400×400 base for remaining variants
    rs = cv2.resize(crop, (400, 400), interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(rs, cv2.COLOR_BGR2GRAY)
    cl = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    cl_gray = cl.apply(gray)
    _, otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    sharpen_k = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]], np.float32)

    variants = [
        ("clahe",       cl_gray),
        ("otsu",        otsu),
        ("sharpen",     np.clip(cv2.filter2D(gray, -1, sharpen_k), 0, 255).astype(np.uint8)),
        ("adaptive",    cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                              cv2.THRESH_BINARY, 11, 2)),
        ("clahe_otsu",  cv2.threshold(cl_gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]),
        ("inv_otsu",    cv2.bitwise_not(otsu)),
        ("morph_close", cv2.morphologyEx(otsu, cv2.MORPH_CLOSE, np.ones((2, 2), np.uint8))),
    ]
    # NOTE: removed "denoise" (fastNlMeansDenoising) — takes 200-500ms, not worth it

    for name, v in variants:
        try:
            r = try_decode(v)
            if r:
                return r, name
        except Exception as e:
            logging.error(f"Variant {name} error: {e}")

    # 3. Rotation — only common tilt angles, not exhaustive
    for angle in [10, -10, 5, -5, 90]:
        try:
            r = try_decode(_rotate(cl_gray, angle))
            if r:
                return r, f"rotation_{angle}"
        except Exception as e:
            logging.error(f"Rotation {angle} error: {e}")

    # 4. Full-frame fallback (crop coordinates might be slightly off)
    r = try_decode(frame_bgr)
    if r:
        return r, "full_frame"

    return None, "all_failed"

def iou(b1, b2):
    ix1, iy1 = max(b1[0], b2[0]), max(b1[1], b2[1])
    ix2, iy2 = min(b1[2], b2[2]), min(b1[3], b2[3])
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    if inter == 0:
        return 0.0
    return inter / ((b1[2] - b1[0]) * (b1[3] - b1[1]) + (b2[2] - b2[0]) * (b2[3] - b2[1]) - inter)
