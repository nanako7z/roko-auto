#!/usr/bin/env python3
"""
Interception-based periodic input runner (Windows).

This uses the Interception driver API (kernel filter driver) to send keyboard
scan-code strokes, which is closer to real device input than SendInput.

Requirements:
1) Install Interception driver on Windows.
2) Ensure interception.dll is accessible (same folder as this script or in PATH).
3) pip install pyyaml

Run:
  python interception_runner.py --config config.yaml
"""

from __future__ import annotations

import argparse
import ctypes
import math
import random
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Union

import yaml


# Interception keyboard constants (from interception.h)
INTERCEPTION_KEY_DOWN = 0x00
INTERCEPTION_KEY_UP = 0x01
INTERCEPTION_KEY_E0 = 0x02

# Interception mouse state flags (from interception.h)
INTERCEPTION_MOUSE_LEFT_BUTTON_DOWN   = 0x001
INTERCEPTION_MOUSE_LEFT_BUTTON_UP     = 0x002
INTERCEPTION_MOUSE_RIGHT_BUTTON_DOWN  = 0x004
INTERCEPTION_MOUSE_RIGHT_BUTTON_UP    = 0x008
INTERCEPTION_MOUSE_MIDDLE_BUTTON_DOWN = 0x010
INTERCEPTION_MOUSE_MIDDLE_BUTTON_UP   = 0x020
INTERCEPTION_MOUSE_WHEEL              = 0x400
INTERCEPTION_MOUSE_MOVE_RELATIVE      = 0x000  # flags field
INTERCEPTION_MOUSE_MOVE_ABSOLUTE      = 0x001  # flags field


@dataclass
class Schedule:
    interval_sec: float
    jitter_sec: float = 0.0
    start_delay_sec: float = 0.0


class InterceptionKeyStroke(ctypes.Structure):
    _fields_ = [
        ("code", ctypes.c_ushort),
        ("state", ctypes.c_ushort),
        ("information", ctypes.c_uint),
    ]


class InterceptionMouseStroke(ctypes.Structure):
    _fields_ = [
        ("state",       ctypes.c_ushort),
        ("flags",       ctypes.c_ushort),
        ("rolling",     ctypes.c_short),
        ("x",           ctypes.c_int),
        ("y",           ctypes.c_int),
        ("information", ctypes.c_uint),
    ]


# Win32 cursor position
class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


# Win32 SendInput structures (fallback, 64-bit Windows)
class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_void_p),  # ULONG_PTR
    ]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx",          ctypes.c_long),
        ("dy",          ctypes.c_long),
        ("mouseData",   ctypes.c_ulong),
        ("dwFlags",     ctypes.c_ulong),
        ("time",        ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_void_p),  # ULONG_PTR
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("ki", _KEYBDINPUT),
        ("mi", _MOUSEINPUT),
    ]


class _INPUT(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_ulong),
        ("union", _INPUT_UNION),
    ]


_INPUT_MOUSE            = 0
_INPUT_KEYBOARD         = 1
_KEYEVENTF_SCANCODE     = 0x0008
_KEYEVENTF_KEYUP        = 0x0002
_KEYEVENTF_EXTENDEDKEY  = 0x0001
_MOUSEEVENTF_MOVE       = 0x0001
_MOUSEEVENTF_ABSOLUTE   = 0x8000
_MOUSEEVENTF_LEFTDOWN   = 0x0002
_MOUSEEVENTF_LEFTUP     = 0x0004
_MOUSEEVENTF_RIGHTDOWN  = 0x0008
_MOUSEEVENTF_RIGHTUP    = 0x0010
_MOUSEEVENTF_MIDDLEDOWN = 0x0020
_MOUSEEVENTF_MIDDLEUP   = 0x0040
_MOUSEEVENTF_WHEEL      = 0x0800


class SendInputKeyboard:
    """Fallback: uses Win32 SendInput with scan codes (no Interception driver needed)."""

    def __init__(self) -> None:
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._user32.SendInput.argtypes = [
            ctypes.c_uint,
            ctypes.POINTER(_INPUT),
            ctypes.c_int,
        ]
        self._user32.SendInput.restype = ctypes.c_uint

    def send_scan(self, scan_code: int, key_up: bool = False, e0: bool = False) -> None:
        flags = _KEYEVENTF_SCANCODE
        if key_up:
            flags |= _KEYEVENTF_KEYUP
        if e0:
            flags |= _KEYEVENTF_EXTENDEDKEY
        inp = _INPUT(
            type=_INPUT_KEYBOARD,
            union=_INPUT_UNION(ki=_KEYBDINPUT(wVk=0, wScan=scan_code, dwFlags=flags, time=0, dwExtraInfo=0)),
        )
        sent = self._user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))
        if sent != 1:
            raise RuntimeError(f"SendInput failed (error={ctypes.get_last_error()})")

    def tap_scan(self, scan_code: int, hold_sec: float = 0.03, e0: bool = False) -> None:
        self.send_scan(scan_code, key_up=False, e0=e0)
        time.sleep(max(0.0, hold_sec))
        self.send_scan(scan_code, key_up=True, e0=e0)

    def close(self) -> None:
        pass


_MOUSE_BUTTON_DOWN: Dict[str, int] = {
    "left":   INTERCEPTION_MOUSE_LEFT_BUTTON_DOWN,
    "right":  INTERCEPTION_MOUSE_RIGHT_BUTTON_DOWN,
    "middle": INTERCEPTION_MOUSE_MIDDLE_BUTTON_DOWN,
}
_MOUSE_BUTTON_UP: Dict[str, int] = {
    "left":   INTERCEPTION_MOUSE_LEFT_BUTTON_UP,
    "right":  INTERCEPTION_MOUSE_RIGHT_BUTTON_UP,
    "middle": INTERCEPTION_MOUSE_MIDDLE_BUTTON_UP,
}


class InterceptionMouse:
    def __init__(self, dll_path: str = "interception.dll") -> None:
        self.lib = ctypes.WinDLL(dll_path)

        self.lib.interception_create_context.restype = ctypes.c_void_p
        self.lib.interception_destroy_context.argtypes = [ctypes.c_void_p]

        self.lib.interception_send.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.POINTER(InterceptionMouseStroke),
            ctypes.c_uint,
        ]
        self.lib.interception_send.restype = ctypes.c_int

        self.lib.interception_is_mouse.argtypes = [ctypes.c_int]
        self.lib.interception_is_mouse.restype = ctypes.c_int

        self.context = self.lib.interception_create_context()
        if not self.context:
            raise RuntimeError("Failed to create Interception context (mouse)")

        self.mouse_device = self._pick_mouse_device()

    def close(self) -> None:
        if self.context:
            self.lib.interception_destroy_context(self.context)
            self.context = None

    def _pick_mouse_device(self) -> int:
        # Interception mouse device id range: 21..40
        for device in range(21, 41):
            if self.lib.interception_is_mouse(device):
                return device
        raise RuntimeError("No mouse device found by Interception")

    def _send(self, state: int, flags: int = INTERCEPTION_MOUSE_MOVE_RELATIVE,
              rolling: int = 0, x: int = 0, y: int = 0) -> None:
        stroke = InterceptionMouseStroke(state=state, flags=flags, rolling=rolling,
                                         x=x, y=y, information=0)
        sent = self.lib.interception_send(self.context, self.mouse_device,
                                          ctypes.byref(stroke), 1)
        if sent != 1:
            raise RuntimeError("interception_send (mouse) failed")

    def click(self, button: str, hold_sec: float = 0.03) -> None:
        if button not in _MOUSE_BUTTON_DOWN:
            raise ValueError(f"Unsupported mouse button: {button!r} (use left/right/middle)")
        self._send(_MOUSE_BUTTON_DOWN[button])
        time.sleep(max(0.0, hold_sec))
        self._send(_MOUSE_BUTTON_UP[button])

    def move(self, x: int, y: int) -> None:
        self._send(state=0, x=x, y=y)

    def move_to(self, x: int, y: int) -> None:
        nx, ny = _pixel_to_norm(x, y)
        self._send(state=0, flags=INTERCEPTION_MOUSE_MOVE_ABSOLUTE, x=nx, y=ny)

    def scroll(self, amount: int) -> None:
        self._send(INTERCEPTION_MOUSE_WHEEL, rolling=amount * 120)


_SENDINPUT_BUTTON_DOWN: Dict[str, int] = {
    "left":   _MOUSEEVENTF_LEFTDOWN,
    "right":  _MOUSEEVENTF_RIGHTDOWN,
    "middle": _MOUSEEVENTF_MIDDLEDOWN,
}
_SENDINPUT_BUTTON_UP: Dict[str, int] = {
    "left":   _MOUSEEVENTF_LEFTUP,
    "right":  _MOUSEEVENTF_RIGHTUP,
    "middle": _MOUSEEVENTF_MIDDLEUP,
}


class SendInputMouse:
    """Fallback: uses Win32 SendInput for mouse (no Interception driver needed)."""

    def __init__(self) -> None:
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._user32.SendInput.argtypes = [
            ctypes.c_uint,
            ctypes.POINTER(_INPUT),
            ctypes.c_int,
        ]
        self._user32.SendInput.restype = ctypes.c_uint

    def _send(self, flags: int, dx: int = 0, dy: int = 0, mouse_data: int = 0) -> None:
        inp = _INPUT(
            type=_INPUT_MOUSE,
            union=_INPUT_UNION(mi=_MOUSEINPUT(dx=dx, dy=dy, mouseData=mouse_data,
                                              dwFlags=flags, time=0, dwExtraInfo=0)),
        )
        sent = self._user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))
        if sent != 1:
            raise RuntimeError(f"SendInput (mouse) failed (error={ctypes.get_last_error()})")

    def click(self, button: str, hold_sec: float = 0.03) -> None:
        if button not in _SENDINPUT_BUTTON_DOWN:
            raise ValueError(f"Unsupported mouse button: {button!r} (use left/right/middle)")
        self._send(_SENDINPUT_BUTTON_DOWN[button])
        time.sleep(max(0.0, hold_sec))
        self._send(_SENDINPUT_BUTTON_UP[button])

    def move(self, x: int, y: int) -> None:
        self._send(_MOUSEEVENTF_MOVE, dx=x, dy=y)

    def move_to(self, x: int, y: int) -> None:
        nx, ny = _pixel_to_norm(x, y)
        self._send(_MOUSEEVENTF_MOVE | _MOUSEEVENTF_ABSOLUTE, dx=nx, dy=ny)

    def scroll(self, amount: int) -> None:
        self._send(_MOUSEEVENTF_WHEEL, mouse_data=amount * 120)

    def close(self) -> None:
        pass


class InterceptionKeyboard:
    def __init__(self, dll_path: str = "interception.dll") -> None:
        self.lib = ctypes.WinDLL(dll_path)

        self.lib.interception_create_context.restype = ctypes.c_void_p
        self.lib.interception_destroy_context.argtypes = [ctypes.c_void_p]

        self.lib.interception_send.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.POINTER(InterceptionKeyStroke),
            ctypes.c_uint,
        ]
        self.lib.interception_send.restype = ctypes.c_int

        self.lib.interception_is_keyboard.argtypes = [ctypes.c_int]
        self.lib.interception_is_keyboard.restype = ctypes.c_int

        self.context = self.lib.interception_create_context()
        if not self.context:
            raise RuntimeError("Failed to create Interception context")

        self.keyboard_device = self._pick_keyboard_device()

    def close(self) -> None:
        if self.context:
            self.lib.interception_destroy_context(self.context)
            self.context = None

    def _pick_keyboard_device(self) -> int:
        # Interception device id range: 1..20
        for device in range(1, 21):
            if self.lib.interception_is_keyboard(device):
                return device
        raise RuntimeError("No keyboard device found by Interception")

    def send_scan(self, scan_code: int, key_up: bool = False, e0: bool = False) -> None:
        state = INTERCEPTION_KEY_UP if key_up else INTERCEPTION_KEY_DOWN
        if e0:
            state |= INTERCEPTION_KEY_E0

        stroke = InterceptionKeyStroke(code=scan_code, state=state, information=0)
        sent = self.lib.interception_send(
            self.context,
            self.keyboard_device,
            ctypes.byref(stroke),
            1,
        )
        if sent != 1:
            raise RuntimeError("interception_send failed")

    def tap_scan(self, scan_code: int, hold_sec: float = 0.03, e0: bool = False) -> None:
        self.send_scan(scan_code, key_up=False, e0=e0)
        time.sleep(max(0.0, hold_sec))
        self.send_scan(scan_code, key_up=True, e0=e0)


# Set-1 scan codes (common keys)
KEYMAP: Dict[str, Dict[str, Any]] = {
    "tab": {"scan": 0x0F},
    "enter": {"scan": 0x1C},
    "esc": {"scan": 0x01},
    "space": {"scan": 0x39},
    "backspace": {"scan": 0x0E},
    "up": {"scan": 0x48, "e0": True},
    "down": {"scan": 0x50, "e0": True},
    "left": {"scan": 0x4B, "e0": True},
    "right": {"scan": 0x4D, "e0": True},
    "ctrl": {"scan": 0x1D},
    "shift": {"scan": 0x2A},
    "alt": {"scan": 0x38},
}

# letters a-z
for i, ch in enumerate("qwertyuiop", start=0x10):
    KEYMAP[ch] = {"scan": i}
for i, ch in enumerate("asdfghjkl", start=0x1E):
    KEYMAP[ch] = {"scan": i}
for i, ch in enumerate("zxcvbnm", start=0x2C):
    KEYMAP[ch] = {"scan": i}

# digits 0-9
for k, v in {
    "1": 0x02,
    "2": 0x03,
    "3": 0x04,
    "4": 0x05,
    "5": 0x06,
    "6": 0x07,
    "7": 0x08,
    "8": 0x09,
    "9": 0x0A,
    "0": 0x0B,
}.items():
    KEYMAP[k] = {"scan": v}


def _ease_in_out(t: float) -> float:
    return (1 - math.cos(math.pi * t)) / 2


def _bezier(t: float, p0: float, p1: float, p2: float, p3: float) -> float:
    u = 1 - t
    return u**3 * p0 + 3 * u**2 * t * p1 + 3 * u * t**2 * p2 + t**3 * p3


def _human_move(mouse: Mouse, tx: int, ty: int, duration: float, wobble: float = 0.3) -> None:
    """Move mouse to (tx, ty) along a cubic Bézier curve with ease-in-out timing."""
    user32 = ctypes.WinDLL("user32")
    pt = _POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    sx, sy = pt.x, pt.y

    dist = math.hypot(tx - sx, ty - sy)
    if dist == 0:
        return

    # Steps depend on both duration and distance:
    # long duration -> denser path, long distance -> smoother travel.
    duration_steps = int(max(duration, 0.0) * 120)
    distance_steps = int(dist / 7)
    steps = max(10, min(300, max(duration_steps, distance_steps)))

    # Perpendicular unit vector for control point offsets
    px, py = -(ty - sy) / dist, (tx - sx) / dist
    # Keep short-distance moves nearly straight; emphasize curvature for long moves.
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

    # Final residual correction after integer rounding in relative moves.
    user32.GetCursorPos(ctypes.byref(pt))
    rem_x, rem_y = tx - pt.x, ty - pt.y
    if rem_x != 0 or rem_y != 0:
        mouse.move(rem_x, rem_y)


def _pixel_to_norm(x: int, y: int) -> tuple:
    """Convert pixel coordinates to Win32/Interception normalized range (0–65535)."""
    user32 = ctypes.WinDLL("user32")
    sw = user32.GetSystemMetrics(0)
    sh = user32.GetSystemMetrics(1)
    return (x * 65535 // max(sw - 1, 1), y * 65535 // max(sh - 1, 1))


def resolve_key(key: str) -> Dict[str, Any]:
    name = key.strip().lower()
    if name not in KEYMAP:
        raise ValueError(f"Unsupported key: {key!r}")
    return KEYMAP[name]


def load_config(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def resolve_dll_path(dll_path: str, config_path: Path) -> str:
    dll_name = Path(dll_path).name
    candidates: List[Path] = []

    # PyInstaller onefile extracts bundled binaries to _MEIPASS.
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / dll_name)

    raw = Path(dll_path)
    if raw.is_absolute():
        candidates.append(raw)
    else:
        candidates.extend(
            [
                config_path.parent / raw,
                Path(__file__).resolve().parent / raw,
                Path(sys.executable).resolve().parent / raw,
                raw,
            ]
        )

    for path in candidates:
        if path.exists():
            return str(path)

    # Fallback to original value so WinDLL can still try PATH lookup.
    return dll_path


def validate_schedule(data: Dict[str, Any]) -> Schedule:
    raw = data.get("schedule", {})
    interval = float(raw.get("interval_sec", 0))
    jitter = float(raw.get("jitter_sec", 0))
    start_delay = float(raw.get("start_delay_sec", 0))

    if interval <= 0:
        raise ValueError("schedule.interval_sec must be > 0")
    if jitter < 0:
        raise ValueError("schedule.jitter_sec must be >= 0")
    if start_delay < 0:
        raise ValueError("schedule.start_delay_sec must be >= 0")

    return Schedule(interval_sec=interval, jitter_sec=jitter, start_delay_sec=start_delay)


Keyboard = Union[InterceptionKeyboard, SendInputKeyboard]
Mouse = Union[InterceptionMouse, SendInputMouse]


def key_down(kbd: Keyboard, key_name: str) -> None:
    info = resolve_key(key_name)
    kbd.send_scan(info["scan"], key_up=False, e0=bool(info.get("e0", False)))


def key_up(kbd: Keyboard, key_name: str) -> None:
    info = resolve_key(key_name)
    kbd.send_scan(info["scan"], key_up=True, e0=bool(info.get("e0", False)))


def key_tap(kbd: Keyboard, key_name: str, hold_sec: float) -> None:
    info = resolve_key(key_name)
    kbd.tap_scan(info["scan"], hold_sec=max(0.0, hold_sec), e0=bool(info.get("e0", False)))


def execute_commands(
    kbd: Keyboard,
    mouse: Mouse,
    commands: List[Dict[str, Any]],
    default_hold_sec: float,
    mouse_move_default_duration_sec: float,
    mouse_move_default_wobble: float,
) -> None:
    for idx, cmd in enumerate(commands, start=1):
        ctype = str(cmd.get("type", "")).strip().lower()

        if ctype == "wait":
            sec = float(cmd.get("sec", 0))
            if sec < 0:
                raise ValueError(f"commands[{idx}].sec must be >= 0")
            time.sleep(sec)
            continue

        if ctype == "key":
            key_name = str(cmd.get("key", "")).strip()
            if not key_name:
                raise ValueError(f"commands[{idx}].key is required")
            hold_sec = float(cmd.get("hold_sec", default_hold_sec))
            key_tap(kbd, key_name, hold_sec)
            continue

        if ctype == "hotkey":
            keys = cmd.get("keys", [])
            if not isinstance(keys, list) or not keys:
                raise ValueError(f"commands[{idx}].keys must be a non-empty list")

            normalized = [str(k).strip() for k in keys if str(k).strip()]
            if not normalized:
                raise ValueError(f"commands[{idx}].keys must include valid key names")

            # Resolve all keys before pressing any to avoid stuck keys on error
            resolved = [resolve_key(k) for k in normalized]

            for info in resolved:
                kbd.send_scan(info["scan"], key_up=False, e0=bool(info.get("e0", False)))
            time.sleep(float(cmd.get("hold_sec", default_hold_sec)))
            for info in reversed(resolved):
                kbd.send_scan(info["scan"], key_up=True, e0=bool(info.get("e0", False)))
            continue

        if ctype == "mouse_click":
            button = str(cmd.get("button", "")).strip().lower()
            if not button:
                raise ValueError(f"commands[{idx}].button is required")
            hold_sec = float(cmd.get("hold_sec", default_hold_sec))
            mouse.click(button, hold_sec)
            continue

        if ctype == "mouse_move":
            x = int(cmd.get("x", 0))
            y = int(cmd.get("y", 0))
            duration = float(cmd.get("duration", mouse_move_default_duration_sec))
            if cmd.get("absolute", False):
                if duration > 0:
                    wobble = float(cmd.get("wobble", mouse_move_default_wobble))
                    _human_move(mouse, x, y, duration, wobble)
                else:
                    mouse.move_to(x, y)
            else:
                mouse.move(x, y)
            continue

        if ctype == "mouse_scroll":
            amount = int(cmd.get("amount", 0))
            if amount == 0:
                raise ValueError(f"commands[{idx}].amount must be non-zero")
            mouse.scroll(amount)
            continue

        raise ValueError(f"commands[{idx}] has unsupported type: {ctype!r}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Interception periodic input runner")
    parser.add_argument("--config", default="config.yaml", help="Path to YAML config")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")

    cfg = load_config(config_path)
    schedule = validate_schedule(cfg)

    commands = cfg.get("commands", [])
    if not isinstance(commands, list) or not commands:
        raise ValueError("commands must be a non-empty list")

    options = cfg.get("options", {})
    default_hold_sec = float(options.get("default_hold_sec", 0.03))
    pause_between_cycles = float(options.get("pause_between_cycles_sec", 0.0))
    mouse_move_default_duration_sec = float(options.get("mouse_move_default_duration_sec", 0))
    mouse_move_default_wobble = float(options.get("mouse_move_default_wobble", 0.2))

    if default_hold_sec < 0:
        raise ValueError("options.default_hold_sec must be >= 0")
    if pause_between_cycles < 0:
        raise ValueError("options.pause_between_cycles_sec must be >= 0")
    if mouse_move_default_duration_sec < 0:
        raise ValueError("options.mouse_move_default_duration_sec must be >= 0")
    if mouse_move_default_wobble < 0:
        raise ValueError("options.mouse_move_default_wobble must be >= 0")

    driver = str(cfg.get("driver", {}).get("dll_path", "interception.dll"))
    resolved_driver = resolve_dll_path(driver, config_path)

    if schedule.start_delay_sec > 0:
        print(f"[INFO] Starting in {schedule.start_delay_sec:.2f}s...")
        time.sleep(schedule.start_delay_sec)

    print(f"[INFO] Using driver DLL: {resolved_driver}")
    try:
        kbd = InterceptionKeyboard(dll_path=resolved_driver)
        print("[INFO] Interception driver active.")
    except (RuntimeError, OSError) as exc:
        print("=" * 60)
        print("[WARN] Interception driver unavailable:")
        print(f"       {exc}")
        print("[WARN] Falling back to Win32 SendInput.")
        print("[WARN] Input will NOT be injected at the driver level.")
        print("[WARN] Anticheat / input validation may detect this.")
        print("=" * 60)
        kbd = SendInputKeyboard()

    try:
        mouse = InterceptionMouse(dll_path=resolved_driver)
        print("[INFO] Interception mouse device active.")
    except (RuntimeError, OSError) as exc:
        print("=" * 60)
        print("[WARN] Interception mouse unavailable:")
        print(f"       {exc}")
        print("[WARN] Falling back to Win32 SendInput for mouse.")
        print("[WARN] Input will NOT be injected at the driver level.")
        print("[WARN] Anticheat / input validation may detect this.")
        print("=" * 60)
        mouse = SendInputMouse()

    cycle = 0

    try:
        kbd_label   = "Interception" if isinstance(kbd,   InterceptionKeyboard) else "SendInput"
        mouse_label = "Interception" if isinstance(mouse, InterceptionMouse)    else "SendInput"
        print(f"[INFO] Running — keyboard: {kbd_label}, mouse: {mouse_label}. Press Ctrl+C to stop.")
        while True:
            cycle += 1
            started_at = time.time()
            print(f"[INFO] Cycle {cycle} started")

            execute_commands(
                kbd,
                mouse,
                commands,
                default_hold_sec=default_hold_sec,
                mouse_move_default_duration_sec=mouse_move_default_duration_sec,
                mouse_move_default_wobble=mouse_move_default_wobble,
            )

            if args.once:
                print("[INFO] --once set, exiting.")
                return

            jitter = random.uniform(-schedule.jitter_sec, schedule.jitter_sec) if schedule.jitter_sec else 0.0
            target_interval = max(0.0, schedule.interval_sec + jitter)
            elapsed = time.time() - started_at
            sleep_sec = max(0.0, target_interval - elapsed)

            if pause_between_cycles > 0:
                sleep_sec += pause_between_cycles

            print(
                f"[INFO] Cycle {cycle} done | elapsed={elapsed:.2f}s | "
                f"next_in={sleep_sec:.2f}s (target_interval={target_interval:.2f}s)"
            )
            time.sleep(sleep_sec)
    finally:
        kbd.close()
        mouse.close()


if __name__ == "__main__":
    main()
