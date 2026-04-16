"""Constants, ctypes structures, and key mappings for Interception and Win32 input."""

from __future__ import annotations

import ctypes
from typing import Any, Dict

# ---------------------------------------------------------------------------
# Interception keyboard constants (from interception.h)
# ---------------------------------------------------------------------------
INTERCEPTION_KEY_DOWN = 0x00
INTERCEPTION_KEY_UP = 0x01
INTERCEPTION_KEY_E0 = 0x02

# ---------------------------------------------------------------------------
# Interception mouse state flags (from interception.h)
# ---------------------------------------------------------------------------
INTERCEPTION_MOUSE_LEFT_BUTTON_DOWN   = 0x001
INTERCEPTION_MOUSE_LEFT_BUTTON_UP     = 0x002
INTERCEPTION_MOUSE_RIGHT_BUTTON_DOWN  = 0x004
INTERCEPTION_MOUSE_RIGHT_BUTTON_UP    = 0x008
INTERCEPTION_MOUSE_MIDDLE_BUTTON_DOWN = 0x010
INTERCEPTION_MOUSE_MIDDLE_BUTTON_UP   = 0x020
INTERCEPTION_MOUSE_WHEEL              = 0x400
INTERCEPTION_MOUSE_MOVE_RELATIVE      = 0x000
INTERCEPTION_MOUSE_MOVE_ABSOLUTE      = 0x001

# Interception filter constants
INTERCEPTION_FILTER_KEY_ALL = 0xFFFF
INTERCEPTION_FILTER_MOUSE_ALL = 0xFFFF
INTERCEPTION_PREDICATE = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int)

# ---------------------------------------------------------------------------
# ctypes structures — Interception strokes
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Win32 structures (for SendInput fallback)
# ---------------------------------------------------------------------------

class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx",          ctypes.c_long),
        ("dy",          ctypes.c_long),
        ("mouseData",   ctypes.c_ulong),
        ("dwFlags",     ctypes.c_ulong),
        ("time",        ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_void_p),
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


# Win32 SendInput constants
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

# ---------------------------------------------------------------------------
# Interception button maps
# ---------------------------------------------------------------------------
MOUSE_BUTTON_DOWN: Dict[str, int] = {
    "left":   INTERCEPTION_MOUSE_LEFT_BUTTON_DOWN,
    "right":  INTERCEPTION_MOUSE_RIGHT_BUTTON_DOWN,
    "middle": INTERCEPTION_MOUSE_MIDDLE_BUTTON_DOWN,
}
MOUSE_BUTTON_UP: Dict[str, int] = {
    "left":   INTERCEPTION_MOUSE_LEFT_BUTTON_UP,
    "right":  INTERCEPTION_MOUSE_RIGHT_BUTTON_UP,
    "middle": INTERCEPTION_MOUSE_MIDDLE_BUTTON_UP,
}

# SendInput button maps
SENDINPUT_BUTTON_DOWN: Dict[str, int] = {
    "left":   _MOUSEEVENTF_LEFTDOWN,
    "right":  _MOUSEEVENTF_RIGHTDOWN,
    "middle": _MOUSEEVENTF_MIDDLEDOWN,
}
SENDINPUT_BUTTON_UP: Dict[str, int] = {
    "left":   _MOUSEEVENTF_LEFTUP,
    "right":  _MOUSEEVENTF_RIGHTUP,
    "middle": _MOUSEEVENTF_MIDDLEUP,
}

# SendInput interception-to-sendinput state mapping (for replay)
INTERCEPTION_TO_SENDINPUT_MAP = {
    INTERCEPTION_MOUSE_LEFT_BUTTON_DOWN:   _MOUSEEVENTF_LEFTDOWN,
    INTERCEPTION_MOUSE_LEFT_BUTTON_UP:     _MOUSEEVENTF_LEFTUP,
    INTERCEPTION_MOUSE_RIGHT_BUTTON_DOWN:  _MOUSEEVENTF_RIGHTDOWN,
    INTERCEPTION_MOUSE_RIGHT_BUTTON_UP:    _MOUSEEVENTF_RIGHTUP,
    INTERCEPTION_MOUSE_MIDDLE_BUTTON_DOWN: _MOUSEEVENTF_MIDDLEDOWN,
    INTERCEPTION_MOUSE_MIDDLE_BUTTON_UP:   _MOUSEEVENTF_MIDDLEUP,
}

# ---------------------------------------------------------------------------
# Key map — Set-1 scan codes
# ---------------------------------------------------------------------------
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
    "f12": {"scan": 0x58},
}

# Letters a-z
for _i, _ch in enumerate("qwertyuiop", start=0x10):
    KEYMAP[_ch] = {"scan": _i}
for _i, _ch in enumerate("asdfghjkl", start=0x1E):
    KEYMAP[_ch] = {"scan": _i}
for _i, _ch in enumerate("zxcvbnm", start=0x2C):
    KEYMAP[_ch] = {"scan": _i}

# Digits 0-9
for _k, _v in {"1": 0x02, "2": 0x03, "3": 0x04, "4": 0x05, "5": 0x06,
                "6": 0x07, "7": 0x08, "8": 0x09, "9": 0x0A, "0": 0x0B}.items():
    KEYMAP[_k] = {"scan": _v}

# Misc
F12_SCAN = 0x58
MAX_FILE_INCLUDE_DEPTH = 10
