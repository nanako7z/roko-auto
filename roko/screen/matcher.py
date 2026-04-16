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


# Confidence above this value triggers early termination in coarse pass.
_EARLY_STOP_CONF = 0.95

# Coarse pass downscale factor for the screenshot (2 = half resolution).
_COARSE_DOWNSAMPLE = 2


class TemplateMatcher:
    """Matches a template image against screenshots using OpenCV.

    Supports transparent PNG templates — alpha channel is used as a mask
    so only opaque pixels participate in matching.

    Uses multi-scale color matching with a two-phase strategy:
      1. **Coarse pass** — grayscale + downsampled screenshot for speed.
      2. **Refine pass** — full-resolution BGR around the best scale for accuracy.
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
            self._template_bgr = img[:, :, :3]
            self._mask = (alpha > 0).astype(np.uint8) * 255
            self._has_mask = True
        else:
            self._template_bgr = img if img.ndim == 3 else cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
            self._mask = None
            self._has_mask = False

        self._template_gray = cv2.cvtColor(self._template_bgr, cv2.COLOR_BGR2GRAY)
        self._h, self._w = self._template_bgr.shape[:2]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resize_template(
        self, scale: float, *, gray: bool
    ) -> tuple[np.ndarray, Optional[np.ndarray], int, int]:
        """Return (template, mask_or_None, tw, th) at *scale*."""
        src = self._template_gray if gray else self._template_bgr

        if scale == 1.0:
            h, w = src.shape[:2]
            return src, self._mask, w, h

        new_w = max(1, int(self._w * scale))
        new_h = max(1, int(self._h * scale))
        interp = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
        tmpl = cv2.resize(src, (new_w, new_h), interpolation=interp)
        mask = (
            cv2.resize(self._mask, (new_w, new_h), interpolation=cv2.INTER_NEAREST)
            if self._has_mask
            else None
        )
        return tmpl, mask, new_w, new_h

    @staticmethod
    def _run_match(
        screen: np.ndarray,
        tmpl: np.ndarray,
        mask: Optional[np.ndarray],
        has_mask: bool,
    ) -> tuple[float, tuple[int, int]]:
        """Run matchTemplate and return (confidence, (x, y))."""
        if has_mask and mask is not None:
            # Mask must have same channel count as template
            if tmpl.ndim == 3 and mask.ndim == 2:
                mask = cv2.merge([mask, mask, mask])
            result = cv2.matchTemplate(screen, tmpl, cv2.TM_CCORR_NORMED, mask=mask)
        else:
            result = cv2.matchTemplate(screen, tmpl, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        return float(max_val), max_loc

    def _scale_range(self, screen_w: int, screen_h: int) -> tuple[float, float]:
        """Compute valid (min_scale, max_scale) for the template vs screen."""
        min_dim = min(self._w, self._h)
        min_scale = max(0.1, 16.0 / min_dim) if min_dim > 0 else 0.1
        max_scale_w = screen_w / self._w if self._w > 0 else 10.0
        max_scale_h = screen_h / self._h if self._h > 0 else 10.0
        max_scale = min(2.0, max_scale_w, max_scale_h)
        return min_scale, max_scale

    # ------------------------------------------------------------------
    # Multi-scale matching
    # ------------------------------------------------------------------

    def _multiscale_match(self, screen_bgr: np.ndarray) -> Optional[MatchResult]:
        screen_h, screen_w = screen_bgr.shape[:2]

        # --- Fast path: template is already reasonably sized ---------------
        if self._w <= screen_w and self._h <= screen_h:
            area_ratio = (self._w * self._h) / (screen_w * screen_h)
            if area_ratio < 0.25:
                conf, loc = self._run_match(
                    screen_bgr, self._template_bgr, self._mask, self._has_mask
                )
                if conf >= self.threshold:
                    x, y = loc
                    return MatchResult(
                        x=x, y=y, width=self._w, height=self._h,
                        center_x=x + self._w // 2, center_y=y + self._h // 2,
                        confidence=conf,
                    )

        # --- Determine scale range -----------------------------------------
        min_scale, max_scale = self._scale_range(screen_w, screen_h)
        if min_scale > max_scale:
            return None

        # --- Phase 1: coarse pass (grayscale + downsampled) ----------------
        ds = _COARSE_DOWNSAMPLE
        screen_small = cv2.resize(
            screen_bgr, (screen_w // ds, screen_h // ds), interpolation=cv2.INTER_AREA
        )
        screen_small_gray = cv2.cvtColor(screen_small, cv2.COLOR_BGR2GRAY)

        num_coarse = 20
        # Build scales from center outward so common scales are tried first
        linear = np.linspace(min_scale, max_scale, num_coarse)
        mid = len(linear) // 2
        coarse_scales = np.empty_like(linear)
        lo, hi = mid - 1, mid
        idx = 0
        while lo >= 0 or hi < len(linear):
            if hi < len(linear):
                coarse_scales[idx] = linear[hi]
                idx += 1
                hi += 1
            if lo >= 0:
                coarse_scales[idx] = linear[lo]
                idx += 1
                lo -= 1
        coarse_scales = coarse_scales[:idx]

        best_conf = -1.0
        best_scale = 1.0

        for s in coarse_scales:
            effective_scale = s / ds  # template also needs to shrink by ds
            tmpl, mask, tw, th = self._resize_template(effective_scale, gray=True)
            if tw > screen_small_gray.shape[1] or th > screen_small_gray.shape[0]:
                continue
            if tw < 4 or th < 4:
                continue
            conf, _ = self._run_match(screen_small_gray, tmpl, mask, self._has_mask)
            if conf > best_conf:
                best_conf = conf
                best_scale = s
            if conf >= _EARLY_STOP_CONF:
                break

        # --- Phase 2: refine pass (full-res BGR) around best scale ---------
        step = (max_scale - min_scale) / num_coarse
        refine_lo = max(min_scale, best_scale - step)
        refine_hi = min(max_scale, best_scale + step)
        refine_scales = np.linspace(refine_lo, refine_hi, 10)

        best_conf = -1.0
        best_loc = (0, 0)
        best_tw, best_th = self._w, self._h

        for s in refine_scales:
            tmpl, mask, tw, th = self._resize_template(s, gray=False)
            if tw > screen_w or th > screen_h:
                continue
            if tw < 4 or th < 4:
                continue
            conf, loc = self._run_match(screen_bgr, tmpl, mask, self._has_mask)
            if conf > best_conf:
                best_conf = conf
                best_loc = loc
                best_tw, best_th = tw, th
            if conf >= _EARLY_STOP_CONF:
                break

        if best_conf >= self.threshold:
            x, y = best_loc
            return MatchResult(
                x=x, y=y, width=best_tw, height=best_th,
                center_x=x + best_tw // 2, center_y=y + best_th // 2,
                confidence=best_conf,
            )
        return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

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
            cv2.rectangle(screen, (match.x, match.y),
                          (match.x + match.width, match.y + match.height),
                          (0, 255, 0), 2)
            cv2.drawMarker(screen, (match.center_x, match.center_y),
                           (0, 0, 255), cv2.MARKER_CROSS, 20, 2)
        else:
            cv2.putText(screen, "No match",
                        (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                        (0, 0, 255), 2)

        _, png = cv2.imencode(".png", screen)
        return match, png.tobytes()
