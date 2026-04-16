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

    Uses a 3-phase matching strategy:
      Phase 0: Direct match at scale 1.0 (fast path)
      Phase 1: Coarse multi-scale at half resolution (7 scales)
      Phase 2: Fine refinement (3 scales, full resolution, tight ROI)
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
            self._mask3 = cv2.merge([self._mask, self._mask, self._mask])
            self._has_mask = True
        else:
            self._template_bgr = img if img.ndim == 3 else cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
            self._mask = None
            self._mask3 = None
            self._has_mask = False

        self._h, self._w = self._template_bgr.shape[:2]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resize_template_with_mask3(
        self, scale: float
    ) -> tuple[np.ndarray, Optional[np.ndarray], int, int]:
        """Return (template_bgr, mask3_or_None, tw, th) at *scale*."""
        if scale == 1.0:
            return self._template_bgr, self._mask3, self._w, self._h

        new_w = max(1, int(self._w * scale))
        new_h = max(1, int(self._h * scale))
        interp = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
        tmpl = cv2.resize(self._template_bgr, (new_w, new_h), interpolation=interp)

        if self._has_mask:
            m = cv2.resize(self._mask, (new_w, new_h), interpolation=cv2.INTER_NEAREST)
            mask3 = cv2.merge([m, m, m])
        else:
            mask3 = None

        return tmpl, mask3, new_w, new_h

    def _cv_match(
        self,
        screen: np.ndarray,
        tmpl: np.ndarray,
        mask3: Optional[np.ndarray],
    ) -> tuple[float, tuple[int, int]]:
        """Run matchTemplate. Returns (confidence, (x, y)).

        Uses TM_CCORR_NORMED for masked templates (only normalized method
        that supports mask in OpenCV), TM_CCOEFF_NORMED for non-masked.
        """
        if mask3 is not None:
            result = cv2.matchTemplate(screen, tmpl, cv2.TM_CCORR_NORMED, mask=mask3)
        else:
            result = cv2.matchTemplate(screen, tmpl, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        conf = float(max_val)
        if conf != conf:  # NaN guard
            conf = 0.0
        return conf, max_loc

    def _scale_range(self, screen_w: int, screen_h: int) -> tuple[float, float]:
        """Compute valid (min_scale, max_scale) for the template vs screen."""
        min_dim = min(self._w, self._h)
        min_scale = max(0.1, 16.0 / min_dim) if min_dim > 0 else 0.1
        max_scale_w = screen_w / self._w if self._w > 0 else 10.0
        max_scale_h = screen_h / self._h if self._h > 0 else 10.0
        max_scale = min(2.0, max_scale_w, max_scale_h)
        return min_scale, max_scale

    # ------------------------------------------------------------------
    # Phase 0: Direct match at scale 1.0
    # ------------------------------------------------------------------

    def _try_direct_match(self, screen_bgr: np.ndarray) -> Optional[MatchResult]:
        """Try exact scale=1.0 match. Returns MatchResult or None."""
        sh, sw = screen_bgr.shape[:2]
        if self._w > sw or self._h > sh:
            return None

        conf, loc = self._cv_match(screen_bgr, self._template_bgr, self._mask3)
        if conf >= self.threshold:
            x, y = loc
            return MatchResult(
                x=x, y=y, width=self._w, height=self._h,
                center_x=x + self._w // 2, center_y=y + self._h // 2,
                confidence=conf,
            )
        return None

    # ------------------------------------------------------------------
    # Multi-scale scan with iterative refinement
    # ------------------------------------------------------------------

    # Pyramid passes: (downsample_factor, num_scales)
    # Each pass narrows the scale range around the previous best.
    _PASSES = [
        (4, 11),   # Pass 1: 1/4 res, 11 scales — broad sweep
        (2, 7),    # Pass 2: 1/2 res,  7 scales — narrow
        (1, 5),    # Pass 3: full res,  5 scales — precise (with ROI)
    ]

    def _scan_scales(
        self,
        screen: np.ndarray,
        ds: int,
        scales: np.ndarray,
    ) -> tuple[float, float, tuple[int, int], int, int]:
        """Run matchTemplate at each scale on *screen* (already downsampled by ds).

        Returns (best_scale, best_conf, best_loc, tw_at_ds, th_at_ds).
        """
        scr_h, scr_w = screen.shape[:2]

        best_conf = -1.0
        best_scale = float(scales[len(scales) // 2])
        best_loc: tuple[int, int] = (0, 0)
        best_tw, best_th = self._w, self._h

        for s in scales:
            eff_scale = s / ds
            tmpl, mask3, tw, th = self._resize_template_with_mask3(eff_scale)
            if tw < 4 or th < 4 or tw > scr_w or th > scr_h:
                continue

            conf, loc = self._cv_match(screen, tmpl, mask3)
            if conf > best_conf:
                best_conf = conf
                best_scale = float(s)
                best_loc = loc
                best_tw, best_th = tw, th
            if conf >= 0.95:
                break

        return best_scale, best_conf, best_loc, best_tw, best_th

    def _iterative_multiscale(self, screen_bgr: np.ndarray) -> Optional[MatchResult]:
        """Iterative coarse-to-fine multi-scale search.

        Pass 1: 1/4 res, 11 scales over full range — find approximate scale
        Pass 2: 1/2 res,  7 scales over narrowed range — refine scale
        Pass 3: full res,  5 scales over tight range + ROI — precise result
        """
        sh, sw = screen_bgr.shape[:2]
        min_scale, max_scale = self._scale_range(sw, sh)
        if min_scale > max_scale:
            return None

        scale_lo, scale_hi = min_scale, max_scale
        best_scale = (min_scale + max_scale) / 2
        best_conf = -1.0
        best_loc: tuple[int, int] = (0, 0)
        best_tw, best_th = self._w, self._h
        prev_ds = 1

        for ds, num_scales in self._PASSES:
            # Downsample screenshot
            if ds > 1:
                scr = cv2.resize(
                    screen_bgr, (sw // ds, sh // ds),
                    interpolation=cv2.INTER_AREA,
                )
            else:
                scr = screen_bgr

            # For the full-res pass, extract ROI around previous best location
            roi_ox, roi_oy = 0, 0
            if ds == 1 and best_conf > 0:
                ratio = prev_ds  # previous pass downsample factor
                cx = best_loc[0] * ratio + best_tw * ratio // 2
                cy = best_loc[1] * ratio + best_th * ratio // 2
                margin = max(best_tw * ratio * 3, best_th * ratio * 3, 300)
                x0 = max(0, cx - margin)
                y0 = max(0, cy - margin)
                x1 = min(sw, cx + margin)
                y1 = min(sh, cy + margin)
                scr = scr[y0:y1, x0:x1]
                roi_ox, roi_oy = x0, y0

            scales = np.linspace(scale_lo, scale_hi, num_scales)
            p_scale, p_conf, p_loc, p_tw, p_th = self._scan_scales(scr, ds, scales)

            if p_conf > best_conf:
                best_conf = p_conf
                best_scale = p_scale
                best_loc = (p_loc[0] + roi_ox, p_loc[1] + roi_oy) if ds == 1 else p_loc
                best_tw, best_th = p_tw * ds, p_th * ds  # map to full-res dimensions

            # Narrow scale range for next pass
            step = (scale_hi - scale_lo) / max(num_scales - 1, 1)
            scale_lo = max(min_scale, best_scale - step)
            scale_hi = min(max_scale, best_scale + step)
            prev_ds = ds

        if best_conf >= self.threshold:
            # Re-derive template size at best scale for accurate dimensions
            final_tw = max(1, int(self._w * best_scale))
            final_th = max(1, int(self._h * best_scale))
            x, y = best_loc
            return MatchResult(
                x=x, y=y, width=final_tw, height=final_th,
                center_x=x + final_tw // 2, center_y=y + final_th // 2,
                confidence=best_conf,
            )
        return None

    # ------------------------------------------------------------------
    # Multi-scale matching (orchestrator)
    # ------------------------------------------------------------------

    def _multiscale_match(self, screen_bgr: np.ndarray) -> Optional[MatchResult]:
        # Fast path: direct match at scale 1.0
        result = self._try_direct_match(screen_bgr)
        if result is not None:
            return result

        # Iterative coarse-to-fine multi-scale search
        return self._iterative_multiscale(screen_bgr)

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
