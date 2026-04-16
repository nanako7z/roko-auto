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

        # Detect whether this OpenCV build supports mask with TM_CCOEFF_NORMED
        self._use_ccoeff_mask = self._has_mask and self._detect_ccoeff_mask_support()

    def _detect_ccoeff_mask_support(self) -> bool:
        """Test if OpenCV supports TM_CCOEFF_NORMED with mask parameter."""
        try:
            tiny_screen = np.zeros((8, 8, 3), dtype=np.uint8)
            tiny_tmpl = np.zeros((4, 4, 3), dtype=np.uint8)
            tiny_mask = np.ones((4, 4, 3), dtype=np.uint8) * 255
            cv2.matchTemplate(tiny_screen, tiny_tmpl, cv2.TM_CCOEFF_NORMED, mask=tiny_mask)
            return True
        except cv2.error:
            return False

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
        """Run matchTemplate with TM_CCOEFF_NORMED. Returns (confidence, (x, y))."""
        if mask3 is not None:
            if self._use_ccoeff_mask:
                result = cv2.matchTemplate(screen, tmpl, cv2.TM_CCOEFF_NORMED, mask=mask3)
            else:
                result = cv2.matchTemplate(screen, tmpl, cv2.TM_CCORR_NORMED, mask=mask3)
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
    # Phase 1: Coarse multi-scale at half resolution
    # ------------------------------------------------------------------

    def _coarse_scale_scan(
        self, screen_bgr: np.ndarray
    ) -> tuple[float, float, tuple[int, int], int, int]:
        """Scan 7 scales at half resolution.

        Returns (best_scale, best_conf, best_loc_in_half, tw_in_half, th_in_half).
        """
        sh, sw = screen_bgr.shape[:2]
        half_w, half_h = sw // 2, sh // 2
        half_screen = cv2.resize(screen_bgr, (half_w, half_h), interpolation=cv2.INTER_AREA)

        min_scale, max_scale = self._scale_range(sw, sh)
        if min_scale > max_scale:
            return 1.0, -1.0, (0, 0), self._w, self._h

        scales = np.linspace(min_scale, max_scale, 7)

        best_conf = -1.0
        best_scale = 1.0
        best_loc: tuple[int, int] = (0, 0)
        best_tw, best_th = self._w, self._h

        for s in scales:
            eff_scale = s / 2  # template scale for half-resolution screen
            tmpl, mask3, tw, th = self._resize_template_with_mask3(eff_scale)
            if tw < 4 or th < 4 or tw > half_w or th > half_h:
                continue

            conf, loc = self._cv_match(half_screen, tmpl, mask3)
            if conf > best_conf:
                best_conf = conf
                best_scale = s
                best_loc = loc
                best_tw, best_th = tw, th
            if conf >= 0.95:
                break

        return best_scale, best_conf, best_loc, best_tw, best_th

    # ------------------------------------------------------------------
    # Phase 2: Fine refinement at full resolution with ROI
    # ------------------------------------------------------------------

    def _fine_refine(
        self,
        screen_bgr: np.ndarray,
        est_scale: float,
        coarse_loc: tuple[int, int],
        coarse_tw: int,
        coarse_th: int,
    ) -> tuple[float, tuple[int, int], int, int]:
        """Refine around estimated scale at full resolution in a tight ROI.

        Returns (confidence, (x, y) in full coords, tw, th).
        """
        sh, sw = screen_bgr.shape[:2]
        min_scale, max_scale = self._scale_range(sw, sh)

        # 3 fine scales around the estimate
        delta = (max_scale - min_scale) / 14
        fine_scales = [
            max(min_scale, est_scale - delta),
            est_scale,
            min(max_scale, est_scale + delta),
        ]

        # ROI around coarse location (mapped to full resolution: *2)
        cx = coarse_loc[0] * 2 + coarse_tw * 2
        cy = coarse_loc[1] * 2 + coarse_th * 2
        margin = max(coarse_tw * 4, coarse_th * 4, 200)

        x0 = max(0, cx - margin)
        y0 = max(0, cy - margin)
        x1 = min(sw, cx + margin)
        y1 = min(sh, cy + margin)
        roi = screen_bgr[y0:y1, x0:x1]

        best_conf = -1.0
        best_loc: tuple[int, int] = (0, 0)
        best_tw, best_th = self._w, self._h

        for s in fine_scales:
            tmpl, mask3, tw, th = self._resize_template_with_mask3(s)
            if tw > roi.shape[1] or th > roi.shape[0] or tw < 4 or th < 4:
                continue

            conf, loc = self._cv_match(roi, tmpl, mask3)
            if conf > best_conf:
                best_conf = conf
                best_loc = (loc[0] + x0, loc[1] + y0)
                best_tw, best_th = tw, th

        return best_conf, best_loc, best_tw, best_th

    # ------------------------------------------------------------------
    # Multi-scale matching (orchestrator)
    # ------------------------------------------------------------------

    def _multiscale_match(self, screen_bgr: np.ndarray) -> Optional[MatchResult]:
        # Phase 0: direct match at scale 1.0
        result = self._try_direct_match(screen_bgr)
        if result is not None:
            return result

        # Phase 1: coarse scan at half resolution
        est_scale, coarse_conf, coarse_loc, c_tw, c_th = self._coarse_scale_scan(screen_bgr)

        if coarse_conf < 0:
            return None

        # Phase 2: fine refinement at full resolution
        conf, loc, tw, th = self._fine_refine(screen_bgr, est_scale, coarse_loc, c_tw, c_th)

        if conf >= self.threshold:
            x, y = loc
            return MatchResult(
                x=x, y=y, width=tw, height=th,
                center_x=x + tw // 2, center_y=y + th // 2,
                confidence=conf,
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
