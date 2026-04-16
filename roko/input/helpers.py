"""Utility functions: key resolution, human-like mouse movement, coordinate conversion."""

from __future__ import annotations

import ctypes
import math
import random
import time
from typing import Any, Dict, Union

from .constants import KEYMAP, _POINT


def resolve_key(key: str) -> Dict[str, Any]:
    name = key.strip().lower()
    if name not in KEYMAP:
        raise ValueError(f"Unsupported key: {key!r}")
    return KEYMAP[name]


def _ease_in_out(t: float) -> float:
    return (1 - math.cos(math.pi * t)) / 2


def _bezier(t: float, p0: float, p1: float, p2: float, p3: float) -> float:
    u = 1 - t
    return u**3 * p0 + 3 * u**2 * t * p1 + 3 * u * t**2 * p2 + t**3 * p3


# Type alias — accepts either Interception or SendInput mouse
Mouse = Any


def _human_move(mouse: Mouse, tx: int, ty: int, duration: float, wobble: float = 0.3) -> None:
    """Move mouse to (tx, ty) along a cubic Bezier curve with ease-in-out timing."""
    user32 = ctypes.WinDLL("user32")
    pt = _POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    sx, sy = pt.x, pt.y

    dist = math.hypot(tx - sx, ty - sy)
    if dist == 0:
        return

    duration_steps = int(max(duration, 0.0) * 120)
    distance_steps = int(dist / 7)
    steps = max(10, min(300, max(duration_steps, distance_steps)))

    px, py = -(ty - sy) / dist, (tx - sx) / dist
    bend_scale = min(1.0, dist / 250.0)
    offset_base = dist * max(0.0, wobble) * bend_scale
    side = random.choice((-1.0, 1.0))
    c1_offset = side * offset_base * random.uniform(0.5, 1.0)
    c2_offset = -side * offset_base * random.uniform(0.5, 1.0)
    c1x = sx + (tx - sx) * 0.3 + px * c1_offset
    c1y = sy + (ty - sy) * 0.3 + py * c1_offset
    c2x = sx + (tx - sx) * 0.7 + px * c2_offset
    c2y = sy + (ty - sy) * 0.7 + py * c2_offset

    cur_x, cur_y = float(sx), float(sy)
    for i in range(1, steps + 1):
        t = _ease_in_out(i / steps)
        nx = _bezier(t, sx, c1x, c2x, tx)
        ny = _bezier(t, sy, c1y, c2y, ty)
        rel_x = round(nx - cur_x)
        rel_y = round(ny - cur_y)
        if rel_x != 0 or rel_y != 0:
            mouse.move(rel_x, rel_y)
        cur_x, cur_y = nx, ny
        time.sleep(duration / steps)

    user32.GetCursorPos(ctypes.byref(pt))
    rem_x, rem_y = tx - pt.x, ty - pt.y
    if rem_x != 0 or rem_y != 0:
        mouse.move(rem_x, rem_y)


def _pixel_to_norm(x: int, y: int) -> tuple:
    """Convert pixel coordinates to normalized range (0-65535)."""
    user32 = ctypes.WinDLL("user32")
    sw = user32.GetSystemMetrics(0)
    sh = user32.GetSystemMetrics(1)
    return (x * 65535 // max(sw - 1, 1), y * 65535 // max(sh - 1, 1))


def _norm_to_pixel(nx: int, ny: int) -> tuple:
    """Convert normalized coordinates (0-65535) back to pixel coordinates."""
    user32 = ctypes.WinDLL("user32")
    sw = user32.GetSystemMetrics(0)
    sh = user32.GetSystemMetrics(1)
    return (nx * max(sw - 1, 1) // 65535, ny * max(sh - 1, 1) // 65535)
