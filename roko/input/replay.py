"""Binary recording format and replay logic."""

from __future__ import annotations

import ctypes
import struct
import time
from pathlib import Path
from typing import Any

from .constants import (
    INTERCEPTION_KEY_E0,
    INTERCEPTION_KEY_UP,
    INTERCEPTION_MOUSE_MOVE_ABSOLUTE,
    INTERCEPTION_MOUSE_WHEEL,
    INTERCEPTION_TO_SENDINPUT_MAP,
    _MOUSEEVENTF_MOVE,
    _MOUSEEVENTF_WHEEL,
)

# --- Binary recording format ---
_REC_MAGIC = b"RCRD"
_REC_VERSION = 1
_REC_HEADER_FMT = "<4sHI"
_REC_HEADER_SIZE = struct.calcsize(_REC_HEADER_FMT)
_REC_KEY_TYPE = 0x00
_REC_MOUSE_TYPE = 0x01
_REC_KEY_FMT = "<BHHH"
_REC_KEY_SIZE = struct.calcsize(_REC_KEY_FMT)
_REC_MOUSE_FMT = "<BHHHhii"
_REC_MOUSE_SIZE = struct.calcsize(_REC_MOUSE_FMT)


def _write_rec_header(f, count: int) -> None:
    f.write(struct.pack(_REC_HEADER_FMT, _REC_MAGIC, _REC_VERSION, count))


def _write_rec_key(f, delta_ms: int, scan: int, state: int) -> None:
    f.write(struct.pack(_REC_KEY_FMT, _REC_KEY_TYPE, delta_ms, scan, state))


def _write_rec_mouse(f, delta_ms: int, state: int, flags: int,
                     rolling: int, x: int, y: int) -> None:
    f.write(struct.pack(_REC_MOUSE_FMT, _REC_MOUSE_TYPE, delta_ms,
                        state, flags, rolling, x, y))


def _clamp_delta_ms(elapsed_sec: float) -> int:
    """Clamp elapsed time to uint16 range (max 65535 ms)."""
    return min(65535, max(0, int(elapsed_sec * 1000)))


def _norm_to_pixel(nx: int, ny: int) -> tuple:
    """Convert normalized coordinates (0-65535) back to pixel coordinates."""
    user32 = ctypes.WinDLL("user32")
    sw = user32.GetSystemMetrics(0)
    sh = user32.GetSystemMetrics(1)
    return (nx * max(sw - 1, 1) // 65535, ny * max(sh - 1, 1) // 65535)


def replay_recording(kbd: Any, mouse: Any, path: Path) -> None:
    """Replay a binary recording file, sending events with original timing."""
    from .mouse import InterceptionMouse

    with path.open("rb") as f:
        header_data = f.read(_REC_HEADER_SIZE)
        if len(header_data) < _REC_HEADER_SIZE:
            raise ValueError(f"Recording file too small: {path}")
        magic, version, count = struct.unpack(_REC_HEADER_FMT, header_data)
        if magic != _REC_MAGIC:
            raise ValueError(f"Invalid recording file (bad magic): {path}")
        if version != _REC_VERSION:
            raise ValueError(f"Unsupported recording version {version}: {path}")

        print(f"[INFO] Replaying {count} events from {path}")

        ctypes.windll.user32.SetCursorPos(0, 0)

        for i in range(count):
            type_byte = f.read(1)
            if not type_byte:
                print(f"[WARN] Unexpected EOF at event {i + 1}/{count}")
                break
            event_type = type_byte[0]

            if event_type == _REC_KEY_TYPE:
                rest = f.read(_REC_KEY_SIZE - 1)
                if len(rest) < _REC_KEY_SIZE - 1:
                    break
                _, delta_ms, scan, state = struct.unpack(
                    _REC_KEY_FMT, type_byte + rest)
                if delta_ms > 0:
                    time.sleep(delta_ms / 1000.0)
                e0 = bool(state & INTERCEPTION_KEY_E0)
                key_up = bool(state & INTERCEPTION_KEY_UP)
                kbd.send_scan(scan, key_up=key_up, e0=e0)

            elif event_type == _REC_MOUSE_TYPE:
                rest = f.read(_REC_MOUSE_SIZE - 1)
                if len(rest) < _REC_MOUSE_SIZE - 1:
                    break
                _, delta_ms, state, flags, rolling, x, y = struct.unpack(
                    _REC_MOUSE_FMT, type_byte + rest)
                if delta_ms > 0:
                    time.sleep(delta_ms / 1000.0)

                if flags & INTERCEPTION_MOUSE_MOVE_ABSOLUTE:
                    px, py = _norm_to_pixel(x, y)
                    ctypes.windll.user32.SetCursorPos(px, py)
                else:
                    if isinstance(mouse, InterceptionMouse):
                        mouse._send(state=state, flags=flags,
                                    rolling=rolling, x=x, y=y)
                    else:
                        si_flags = 0
                        if x != 0 or y != 0:
                            si_flags |= _MOUSEEVENTF_MOVE
                        for ic_flag, si_flag in INTERCEPTION_TO_SENDINPUT_MAP.items():
                            if state & ic_flag:
                                si_flags |= si_flag
                        if state & INTERCEPTION_MOUSE_WHEEL:
                            si_flags |= _MOUSEEVENTF_WHEEL
                        mouse._send(si_flags, dx=x, dy=y,
                                    mouse_data=rolling)
            else:
                print(f"[WARN] Unknown event type 0x{event_type:02x} at event {i + 1}")
                break

    print(f"[INFO] Replay complete.")
