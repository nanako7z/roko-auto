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
    width: int      # Template width (at matched scale)
    height: int     # Template height (at matched scale)
    center_x: int   # Center x of matched region
    center_y: int   # Center y of matched region
    confidence: float  # Match confidence score


class TemplateMatcher:
    """Matches a template image against screenshots using OpenCV.

    Supports transparent PNG templates — alpha channel is used as a mask
    so only opaque pixels participate in matching.

    Uses multi-scale color matching: the template is resized across a range
    of scales so it can find targets regardless of the template image's
    original resolution.  Matching is performed in BGR color space for
    better discrimination than grayscale.
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
            self._template = img[:, :, :3]
            self._mask = (alpha > 0).astype(np.uint8) * 255
            self._has_mask = True
        else:
            self._template = img if img.ndim == 3 else cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
            self._mask = None
            self._has_mask = False

        self._h, self._w = self._template.shape[:2]

    def _match_at_scale(
        self, screen_bgr: np.ndarray, scale: float
    ) -> tuple[float, tuple[int, int], int, int]:
        """Run template matching at a given scale.

        Returns (confidence, (x, y), scaled_w, scaled_h).
        """
        if scale == 1.0:
            tmpl = self._template
            mask = self._mask
        else:
            new_w = max(1, int(self._w * scale))
            new_h = max(1, int(self._h * scale))
            interp = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
            tmpl = cv2.resize(self._template, (new_w, new_h), interpolation=interp)
            mask = (cv2.resize(self._mask, (new_w, new_h),
                               interpolation=cv2.INTER_NEAREST)
                    if self._has_mask else None)

        th, tw = tmpl.shape[:2]

        # Template must fit inside the screenshot
        if tw > screen_bgr.shape[1] or th > screen_bgr.shape[0]:
            return -1.0, (0, 0), tw, th

        if self._has_mask and mask is not None:
            # Expand single-channel mask to 3-channel for color template
            mask3 = cv2.merge([mask, mask, mask])
            method = cv2.TM_CCORR_NORMED
            result = cv2.matchTemplate(screen_bgr, tmpl, method, mask=mask3)
        else:
            method = cv2.TM_CCOEFF_NORMED
            result = cv2.matchTemplate(screen_bgr, tmpl, method)

        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        return float(max_val), max_loc, tw, th

    def _multiscale_match(
        self, screen_bgr: np.ndarray
    ) -> Optional[MatchResult]:
        """Try matching the template at multiple scales, return the best hit."""
        screen_h, screen_w = screen_bgr.shape[:2]

        # If template already fits and is small relative to the screen,
        # skip multi-scale and just match directly (fast path).
        if self._w <= screen_w and self._h <= screen_h:
            area_ratio = (self._w * self._h) / (screen_w * screen_h)
            if area_ratio < 0.25:
                conf, loc, tw, th = self._match_at_scale(screen_bgr, 1.0)
                if conf >= self.threshold:
                    x, y = loc
                    return MatchResult(
                        x=x, y=y, width=tw, height=th,
                        center_x=x + tw // 2, center_y=y + th // 2,
                        confidence=conf,
                    )

        # Multi-scale: try scales from 10% to 200% (coarse then refine)
        min_dim = min(self._w, self._h)
        # Smallest useful template is ~16px on its shortest side
        min_scale = max(0.1, 16.0 / min_dim) if min_dim > 0 else 0.1
        # Largest scale: template must fit in the screenshot
        max_scale_w = screen_w / self._w if self._w > 0 else 10.0
        max_scale_h = screen_h / self._h if self._h > 0 else 10.0
        max_scale = min(2.0, max_scale_w, max_scale_h)

        if min_scale > max_scale:
            return None

        # Coarse pass: step through scales
        num_steps = 20
        best_conf = -1.0
        best_scale = 1.0
        best_loc = (0, 0)
        best_tw, best_th = self._w, self._h

        scales = np.linspace(min_scale, max_scale, num_steps)
        for s in scales:
            conf, loc, tw, th = self._match_at_scale(screen_bgr, s)
            if conf > best_conf:
                best_conf = conf
                best_scale = s
                best_loc = loc
                best_tw, best_th = tw, th

        # Refine around the best scale with finer steps
        refine_lo = max(min_scale, best_scale - (max_scale - min_scale) / num_steps)
        refine_hi = min(max_scale, best_scale + (max_scale - min_scale) / num_steps)
        for s in np.linspace(refine_lo, refine_hi, 10):
            conf, loc, tw, th = self._match_at_scale(screen_bgr, s)
            if conf > best_conf:
                best_conf = conf
                best_scale = s
                best_loc = loc
                best_tw, best_th = tw, th

        if best_conf >= self.threshold:
            x, y = best_loc
            return MatchResult(
                x=x, y=y, width=best_tw, height=best_th,
                center_x=x + best_tw // 2, center_y=y + best_th // 2,
                confidence=best_conf,
            )
        return None

    def match(self, screenshot_bytes: bytes) -> Optional[MatchResult]:
        """Match template against a screenshot (PNG/JPEG bytes).

        Returns MatchResult if confidence >= threshold, else None.
        """
        arr = np.frombuffer(screenshot_bytes, np.uint8)
        screen = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if screen is None:
            return None

        return self._multiscale_match(screen)

    def match_annotated(self, screenshot_bytes: bytes) -> tuple[Optional[MatchResult], bytes]:
        """Match and return annotated screenshot with match rectangle drawn.

        Returns (result, annotated_png_bytes).
        """
        arr = np.frombuffer(screenshot_bytes, np.uint8)
        screen = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if screen is None:
            return None, screenshot_bytes

        match = self._multiscale_match(screen)

        if match is not None:
            # Draw green rectangle on match
            cv2.rectangle(screen, (match.x, match.y),
                          (match.x + match.width, match.y + match.height),
                          (0, 255, 0), 2)
            # Draw center crosshair
            cv2.drawMarker(screen, (match.center_x, match.center_y),
                           (0, 0, 255), cv2.MARKER_CROSS, 20, 2)
        else:
            # Draw red text indicating no match
            cv2.putText(screen, "No match",
                        (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                        (0, 0, 255), 2)

        _, png = cv2.imencode(".png", screen)
        return match, png.tobytes()
