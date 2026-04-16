"""Mouse input implementations: Interception driver and Win32 SendInput fallback."""

from __future__ import annotations

import ctypes
import time
from typing import Dict

from .constants import (
    INTERCEPTION_MOUSE_MOVE_RELATIVE,
    INTERCEPTION_MOUSE_WHEEL,
    InterceptionMouseStroke,
    MOUSE_BUTTON_DOWN,
    MOUSE_BUTTON_UP,
    SENDINPUT_BUTTON_DOWN,
    SENDINPUT_BUTTON_UP,
    _INPUT,
    _INPUT_UNION,
    _MOUSEINPUT,
    _INPUT_MOUSE,
    _MOUSEEVENTF_MOVE,
    _MOUSEEVENTF_WHEEL,
)


class InterceptionMouse:
    """Mouse input via Interception kernel driver."""

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
        for device in range(11, 21):
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
        if button not in MOUSE_BUTTON_DOWN:
            raise ValueError(f"Unsupported mouse button: {button!r} (use left/right/middle)")
        self._send(MOUSE_BUTTON_DOWN[button])
        time.sleep(max(0.0, hold_sec))
        self._send(MOUSE_BUTTON_UP[button])

    def move(self, x: int, y: int) -> None:
        self._send(state=0, x=x, y=y)

    def move_to(self, x: int, y: int) -> None:
        ctypes.windll.user32.SetCursorPos(x, y)

    def scroll(self, amount: int) -> None:
        self._send(INTERCEPTION_MOUSE_WHEEL, rolling=amount * 120)


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
        if button not in SENDINPUT_BUTTON_DOWN:
            raise ValueError(f"Unsupported mouse button: {button!r} (use left/right/middle)")
        self._send(SENDINPUT_BUTTON_DOWN[button])
        time.sleep(max(0.0, hold_sec))
        self._send(SENDINPUT_BUTTON_UP[button])

    def move(self, x: int, y: int) -> None:
        self._send(_MOUSEEVENTF_MOVE, dx=x, dy=y)

    def move_to(self, x: int, y: int) -> None:
        ctypes.windll.user32.SetCursorPos(x, y)

    def scroll(self, amount: int) -> None:
        self._send(_MOUSEEVENTF_WHEEL, mouse_data=amount * 120)

    def close(self) -> None:
        pass
