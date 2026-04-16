"""Template matching using OpenCV — finds a template image in a screenshot."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

try:
    from numba import njit
    _HAS_NUMBA = True
except ImportError:
    _HAS_NUMBA = False


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


_EARLY_STOP_CONF = 0.95

# Pyramid levels: (downsample_factor, num_scales, roi_padding_factor)
_PYRAMID_LEVELS = [
    (4, 20, None),   # L0: 1/4 res, 20 scales, full image
    (2, 10, 2.0),    # L1: 1/2 res, 10 scales, ROI from L0
    (1, 5,  1.5),    # L2: full res,  5 scales, tight ROI from L1
]


def _center_out_order(n: int) -> list[int]:
    """Return indices 0..n-1 reordered from center outward."""
    mid = n // 2
    order = []
    lo, hi = mid - 1, mid
    while lo >= 0 or hi < n:
        if hi < n:
            order.append(hi)
            hi += 1
        if lo >= 0:
            order.append(lo)
            lo -= 1
    return order


# ------------------------------------------------------------------
# Numba-accelerated SAD with early termination
# ------------------------------------------------------------------

def _make_sad_functions():
    """Build numba-jitted SAD search functions (called once at import)."""

    @njit(cache=True)
    def _sad_search(screen, tmpl):
        """SAD search with per-pixel early exit. Returns (best_sad, x, y)."""
        sh = screen.shape[0]
        sw = screen.shape[1]
        sc = screen.shape[2]
        th = tmpl.shape[0]
        tw = tmpl.shape[1]

        best_sad = np.int64(th) * np.int64(tw) * np.int64(sc) * np.int64(255)
        best_x = np.int32(0)
        best_y = np.int32(0)

        for y in range(sh - th + 1):
            for x in range(sw - tw + 1):
                sad = np.int64(0)
                bail = False
                for ty in range(th):
                    for tx in range(tw):
                        for c in range(sc):
                            sad += abs(np.int32(screen[y + ty, x + tx, c])
                                       - np.int32(tmpl[ty, tx, c]))
                        if sad >= best_sad:
                            bail = True
                            break
                    if bail:
                        break
                if not bail:
                    best_sad = sad
                    best_x = np.int32(x)
                    best_y = np.int32(y)

        return best_sad, best_x, best_y

    @njit(cache=True)
    def _sad_search_masked(screen, tmpl, mask):
        """SAD search with mask and per-pixel early exit."""
        sh = screen.shape[0]
        sw = screen.shape[1]
        sc = screen.shape[2]
        th = tmpl.shape[0]
        tw = tmpl.shape[1]

        # Count opaque pixels for normalization
        n_opaque = np.int64(0)
        for ty in range(th):
            for tx in range(tw):
                if mask[ty, tx] > 0:
                    n_opaque += 1
        if n_opaque == 0:
            return np.int64(0), np.int32(0), np.int32(0)

        best_sad = n_opaque * np.int64(sc) * np.int64(255)
        best_x = np.int32(0)
        best_y = np.int32(0)

        for y in range(sh - th + 1):
            for x in range(sw - tw + 1):
                sad = np.int64(0)
                bail = False
                for ty in range(th):
                    for tx in range(tw):
                        if mask[ty, tx] == 0:
                            continue
                        for c in range(sc):
                            sad += abs(np.int32(screen[y + ty, x + tx, c])
                                       - np.int32(tmpl[ty, tx, c]))
                        if sad >= best_sad:
                            bail = True
                            break
                    if bail:
                        break
                if not bail:
                    best_sad = sad
                    best_x = np.int32(x)
                    best_y = np.int32(y)

        return best_sad, best_x, best_y

    return _sad_search, _sad_search_masked


if _HAS_NUMBA:
    _sad_search, _sad_search_masked = _make_sad_functions()


class TemplateMatcher:
    """Matches a template image against screenshots using OpenCV.

    Supports transparent PNG templates — alpha channel is used as a mask
    so only opaque pixels participate in matching.

    Uses a 3-level pyramid for multi-scale matching:
      L0: 1/4 resolution, 20 scales (center-out), full image
      L1: 1/2 resolution, 10 scales, ROI around L0 position
      L2: full resolution,  5 scales, tight ROI — uses SAD early-exit (numba)
    """

    def __init__(self, template_path: Path, threshold: float = 0.8) -> None:
        self.threshold = threshold

        template_path = Path(template_path)
        if not template_path.exists():
            raise FileNotFoundError(f"Cannot load template image: {template_path}")
        buf = np.fromfile(str(template_path), dtype=np.uint8)
        img = cv2.imdecode(buf, cv2.IMREAD_UNCHANGED)
        if img is None:
            raise FileNotFoundError(f"Cannot load template image: {template_path}")

        if img.ndim == 3 and img.shape[2] == 4:
            alpha = img[:, :, 3]
            self._template_bgr = img[:, :, :3]
            self._mask = (alpha > 0).astype(np.uint8) * 255
            self._has_mask = True
        else:
            self._template_bgr = img if img.ndim == 3 else cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
            self._mask = None
            self._has_mask = False

        self._h, self._w = self._template_bgr.shape[:2]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resize_template(
        self, scale: float
    ) -> tuple[np.ndarray, Optional[np.ndarray], int, int]:
        """Return (template_bgr, mask_or_None, tw, th) at *scale*."""
        if scale == 1.0:
            return self._template_bgr, self._mask, self._w, self._h

        new_w = max(1, int(self._w * scale))
        new_h = max(1, int(self._h * scale))
        interp = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
        tmpl = cv2.resize(self._template_bgr, (new_w, new_h), interpolation=interp)
        mask = (
            cv2.resize(self._mask, (new_w, new_h), interpolation=cv2.INTER_NEAREST)
            if self._has_mask
            else None
        )
        return tmpl, mask, new_w, new_h

    @staticmethod
    def _run_match_cv(
        screen: np.ndarray,
        tmpl: np.ndarray,
        mask: Optional[np.ndarray],
        has_mask: bool,
    ) -> tuple[float, tuple[int, int]]:
        """matchTemplate and return (confidence, (x, y))."""
        if has_mask and mask is not None:
            if tmpl.ndim == 3 and mask.ndim == 2:
                mask = cv2.merge([mask, mask, mask])
            result = cv2.matchTemplate(screen, tmpl, cv2.TM_CCORR_NORMED, mask=mask)
        else:
            result = cv2.matchTemplate(screen, tmpl, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        return float(max_val), max_loc

    @staticmethod
    def _run_match_sad(
        screen: np.ndarray,
        tmpl: np.ndarray,
        mask: Optional[np.ndarray],
        has_mask: bool,
    ) -> tuple[float, tuple[int, int]]:
        """SAD early-exit search via numba. Returns (confidence, (x, y)).

        confidence = 1.0 - SAD / max_possible_SAD.
        """
        # Ensure contiguous arrays for numba
        screen_c = np.ascontiguousarray(screen)
        tmpl_c = np.ascontiguousarray(tmpl)

        if has_mask and mask is not None:
            mask_c = np.ascontiguousarray(mask)
            best_sad, bx, by = _sad_search_masked(screen_c, tmpl_c, mask_c)
            n_opaque = int(np.count_nonzero(mask))
            max_sad = n_opaque * screen.shape[2] * 255
        else:
            best_sad, bx, by = _sad_search(screen_c, tmpl_c)
            th, tw = tmpl.shape[:2]
            max_sad = th * tw * screen.shape[2] * 255

        conf = 1.0 - (best_sad / max_sad) if max_sad > 0 else 0.0
        return conf, (int(bx), int(by))

    def _scale_range(self, screen_w: int, screen_h: int) -> tuple[float, float]:
        """Compute valid (min_scale, max_scale) for the template vs screen."""
        min_dim = min(self._w, self._h)
        min_scale = max(0.1, 16.0 / min_dim) if min_dim > 0 else 0.1
        max_scale_w = screen_w / self._w if self._w > 0 else 10.0
        max_scale_h = screen_h / self._h if self._h > 0 else 10.0
        max_scale = min(2.0, max_scale_w, max_scale_h)
        return min_scale, max_scale

    @staticmethod
    def _extract_roi(
        screen: np.ndarray,
        loc: tuple[int, int],
        tw: int, th: int,
        padding: float,
        ds: int,
    ) -> tuple[np.ndarray, int, int]:
        """Extract ROI around *loc*. Returns (roi, x_offset, y_offset)."""
        scr_h, scr_w = screen.shape[:2]
        cx = loc[0] * ds + tw * ds // 2
        cy = loc[1] * ds + th * ds // 2

        margin_x = int(tw * ds * padding)
        margin_y = int(th * ds * padding)

        x0 = max(0, cx - margin_x)
        y0 = max(0, cy - margin_y)
        x1 = min(scr_w, cx + margin_x)
        y1 = min(scr_h, cy + margin_y)

        return screen[y0:y1, x0:x1], x0, y0

    # ------------------------------------------------------------------
    # Multi-scale pyramid matching
    # ------------------------------------------------------------------

    def _multiscale_match(self, screen_bgr: np.ndarray) -> Optional[MatchResult]:
        screen_h, screen_w = screen_bgr.shape[:2]

        # --- Fast path: template already reasonably sized ------------------
        if self._w <= screen_w and self._h <= screen_h:
            area_ratio = (self._w * self._h) / (screen_w * screen_h)
            if area_ratio < 0.25:
                conf, loc = self._run_match_cv(
                    screen_bgr, self._template_bgr, self._mask, self._has_mask
                )
                if conf >= self.threshold:
                    x, y = loc
                    return MatchResult(
                        x=x, y=y, width=self._w, height=self._h,
                        center_x=x + self._w // 2, center_y=y + self._h // 2,
                        confidence=conf,
                    )

        # --- Determine full scale range ------------------------------------
        min_scale, max_scale = self._scale_range(screen_w, screen_h)
        if min_scale > max_scale:
            return None

        best_scale = (min_scale + max_scale) / 2
        best_loc: tuple[int, int] = (0, 0)
        best_tw, best_th = self._w, self._h
        scale_lo, scale_hi = min_scale, max_scale
        prev_ds = 1

        for ds, num_scales, roi_padding in _PYRAMID_LEVELS:
            # Downsample screenshot
            if ds > 1:
                scr = cv2.resize(
                    screen_bgr,
                    (screen_w // ds, screen_h // ds),
                    interpolation=cv2.INTER_AREA,
                )
            else:
                scr = screen_bgr

            # Extract ROI if we have a prior position
            roi_ox, roi_oy = 0, 0
            if roi_padding is not None:
                scr, roi_ox, roi_oy = self._extract_roi(
                    scr, best_loc, best_tw, best_th,
                    roi_padding, prev_ds // ds if prev_ds > ds else 1,
                )

            # Use SAD early-exit for the final level (full res, small ROI)
            use_sad = _HAS_NUMBA and ds == 1

            # Build scale list
            scales = np.linspace(scale_lo, scale_hi, num_scales)
            order = _center_out_order(num_scales) if roi_padding is None else list(range(num_scales))

            level_best_conf = -1.0
            level_best_scale = best_scale
            level_best_loc = (0, 0)
            level_best_tw, level_best_th = self._w, self._h

            for i in order:
                s = scales[i]
                eff_scale = s / ds
                tmpl, mask, tw, th = self._resize_template(eff_scale)
                if tw > scr.shape[1] or th > scr.shape[0]:
                    continue
                if tw < 4 or th < 4:
                    continue

                if use_sad:
                    conf, loc = self._run_match_sad(scr, tmpl, mask, self._has_mask)
                else:
                    conf, loc = self._run_match_cv(scr, tmpl, mask, self._has_mask)

                if conf > level_best_conf:
                    level_best_conf = conf
                    level_best_scale = s
                    level_best_loc = (loc[0] + roi_ox, loc[1] + roi_oy)
                    level_best_tw, level_best_th = tw, th
                if conf >= _EARLY_STOP_CONF:
                    break

            best_scale = level_best_scale
            best_loc = level_best_loc
            best_tw = level_best_tw
            best_th = level_best_th
            prev_ds = ds

            step = (scale_hi - scale_lo) / max(num_scales, 1)
            scale_lo = max(min_scale, best_scale - step)
            scale_hi = min(max_scale, best_scale + step)

        if level_best_conf >= self.threshold:
            x, y = best_loc
            return MatchResult(
                x=x, y=y, width=best_tw, height=best_th,
                center_x=x + best_tw // 2, center_y=y + best_th // 2,
                confidence=level_best_conf,
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
