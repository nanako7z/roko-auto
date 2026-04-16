"""Template matching using OpenCV — finds a template image in a screenshot."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np


@dataclass
class MatchResult:
    """Result of a successful template match."""
    x: int          # Top-left x of matched region
    y: int          # Top-left y of matched region
    width: int      # Template width
    height: int     # Template height
    center_x: int   # Center x of matched region
    center_y: int   # Center y of matched region
    confidence: float  # Match confidence score


class TemplateMatcher:
    """Matches a template image against screenshots using OpenCV.

    Supports transparent PNG templates — alpha channel is used as a mask
    so only opaque pixels participate in matching.
    """

    def __init__(self, template_path: Path, threshold: float = 0.8) -> None:
        self.threshold = threshold

        # Use np.fromfile + imdecode to support non-ASCII (e.g. Chinese) paths on Windows
        template_path = Path(template_path)
        if not template_path.exists():
            raise FileNotFoundError(f"Cannot load template image: {template_path}")
        buf = np.fromfile(str(template_path), dtype=np.uint8)
        img = cv2.imdecode(buf, cv2.IMREAD_UNCHANGED)
        if img is None:
            raise FileNotFoundError(f"Cannot load template image: {template_path}")

        # Separate alpha channel if present (BGRA)
        if img.ndim == 3 and img.shape[2] == 4:
            alpha = img[:, :, 3]
            bgr = img[:, :, :3]
            self._mask = (alpha > 0).astype(np.uint8) * 255
            self._has_mask = True
        else:
            bgr = img
            self._mask = None
            self._has_mask = False

        # Convert template to grayscale
        self._template = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        self._h, self._w = self._template.shape[:2]

    def match(self, screenshot_bytes: bytes) -> Optional[MatchResult]:
        """Match template against a screenshot (PNG/JPEG bytes).

        Returns MatchResult if confidence >= threshold, else None.
        """
        arr = np.frombuffer(screenshot_bytes, np.uint8)
        screen = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if screen is None:
            return None

        screen_gray = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)

        # Choose method based on mask availability
        if self._has_mask:
            method = cv2.TM_CCORR_NORMED
            result = cv2.matchTemplate(screen_gray, self._template, method,
                                       mask=self._mask)
        else:
            method = cv2.TM_CCOEFF_NORMED
            result = cv2.matchTemplate(screen_gray, self._template, method)

        _, max_val, _, max_loc = cv2.minMaxLoc(result)

        if max_val >= self.threshold:
            x, y = max_loc
            return MatchResult(
                x=x,
                y=y,
                width=self._w,
                height=self._h,
                center_x=x + self._w // 2,
                center_y=y + self._h // 2,
                confidence=float(max_val),
            )
        return None

    def match_annotated(self, screenshot_bytes: bytes) -> tuple[Optional[MatchResult], bytes]:
        """Match and return annotated screenshot with match rectangle drawn.

        Returns (result, annotated_png_bytes).
        """
        arr = np.frombuffer(screenshot_bytes, np.uint8)
        screen = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if screen is None:
            return None, screenshot_bytes

        screen_gray = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)

        if self._has_mask:
            method = cv2.TM_CCORR_NORMED
            result = cv2.matchTemplate(screen_gray, self._template, method,
                                       mask=self._mask)
        else:
            method = cv2.TM_CCOEFF_NORMED
            result = cv2.matchTemplate(screen_gray, self._template, method)

        _, max_val, _, max_loc = cv2.minMaxLoc(result)

        match = None
        if max_val >= self.threshold:
            x, y = max_loc
            match = MatchResult(
                x=x, y=y,
                width=self._w, height=self._h,
                center_x=x + self._w // 2,
                center_y=y + self._h // 2,
                confidence=float(max_val),
            )
            # Draw green rectangle on match
            cv2.rectangle(screen, (x, y), (x + self._w, y + self._h),
                          (0, 255, 0), 2)
            # Draw center crosshair
            cv2.drawMarker(screen, (match.center_x, match.center_y),
                           (0, 0, 255), cv2.MARKER_CROSS, 20, 2)
        else:
            # Draw red text indicating no match
            cv2.putText(screen, f"No match (best: {max_val:.2f})",
                        (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                        (0, 0, 255), 2)

        _, png = cv2.imencode(".png", screen)
        return match, png.tobytes()
