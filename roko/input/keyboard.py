"""Keyboard input implementations: Interception driver and Win32 SendInput fallback."""

from __future__ import annotations

import ctypes
import time

from .constants import (
    INTERCEPTION_KEY_DOWN,
    INTERCEPTION_KEY_E0,
    INTERCEPTION_KEY_UP,
    InterceptionKeyStroke,
    _INPUT,
    _INPUT_UNION,
    _KEYBDINPUT,
    _INPUT_KEYBOARD,
    _KEYEVENTF_EXTENDEDKEY,
    _KEYEVENTF_KEYUP,
    _KEYEVENTF_SCANCODE,
)


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


class InterceptionKeyboard:
    """Keyboard input via Interception kernel driver."""

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
