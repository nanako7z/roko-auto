"""Screen capture using mss (with Pillow for format conversion)."""

from __future__ import annotations

import io
import time
from typing import Optional, Tuple

import mss
from PIL import Image


class ScreenCapture:
    """Captures screenshots of the primary monitor."""

    def __init__(self, max_fps: int = 2) -> None:
        self._min_interval = 1.0 / max(1, max_fps)
        self._last_capture = 0.0

    def capture(self, region: Optional[Tuple[int, int, int, int]] = None,
                format: str = "png") -> bytes:
        """Capture screenshot and return as bytes.

        Args:
            region: Optional (left, top, width, height) tuple.
            format: Image format — "png" or "jpeg".

        Returns:
            Image bytes in the requested format.
        """
        # Rate limiting
        now = time.time()
        wait = self._min_interval - (now - self._last_capture)
        if wait > 0:
            time.sleep(wait)

        with mss.mss() as sct:
            if region:
                monitor = {
                    "left": region[0],
                    "top": region[1],
                    "width": region[2],
                    "height": region[3],
                }
            else:
                monitor = sct.monitors[1]  # Primary monitor

            screenshot = sct.grab(monitor)

        self._last_capture = time.time()

        # Convert to PIL Image (mss returns BGRA)
        img = Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")

        buf = io.BytesIO()
        img_format = format.upper()
        if img_format == "JPEG":
            img.save(buf, format="JPEG", quality=85)
        else:
            img.save(buf, format="PNG")

        return buf.getvalue()

    def capture_base64(self, region: Optional[Tuple[int, int, int, int]] = None,
                       format: str = "png") -> str:
        """Capture screenshot and return as base64 string."""
        import base64
        raw = self.capture(region=region, format=format)
        return base64.b64encode(raw).decode("ascii")
