"""Thread-safe shared Interception driver context.

Provides a process-singleton that owns the Interception driver context and
serializes all send operations via a lock, allowing multiple task threads to
share the same keyboard/mouse devices safely.
"""

from __future__ import annotations

import ctypes
import threading
import time
from typing import Optional

from .constants import (
    INTERCEPTION_KEY_DOWN,
    INTERCEPTION_KEY_E0,
    INTERCEPTION_KEY_UP,
    INTERCEPTION_MOUSE_MOVE_RELATIVE,
    INTERCEPTION_MOUSE_WHEEL,
    InterceptionKeyStroke,
    InterceptionMouseStroke,
    MOUSE_BUTTON_DOWN,
    MOUSE_BUTTON_UP,
)


class SharedInterceptionContext:
    """Process-singleton that owns the Interception driver context.

    All keyboard/mouse send operations go through this object and are
    serialized with a lock to prevent interleaved strokes.
    """

    _instance: Optional["SharedInterceptionContext"] = None
    _init_lock = threading.Lock()

    def __init__(self, dll_path: str = "interception.dll") -> None:
        self.lib = ctypes.WinDLL(dll_path)

        self.lib.interception_create_context.restype = ctypes.c_void_p
        self.lib.interception_destroy_context.argtypes = [ctypes.c_void_p]

        self.lib.interception_send.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint,
        ]
        self.lib.interception_send.restype = ctypes.c_int

        self.lib.interception_is_keyboard.argtypes = [ctypes.c_int]
        self.lib.interception_is_keyboard.restype = ctypes.c_int
        self.lib.interception_is_mouse.argtypes = [ctypes.c_int]
        self.lib.interception_is_mouse.restype = ctypes.c_int

        self.lib.interception_get_hardware_id.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint,
        ]
        self.lib.interception_get_hardware_id.restype = ctypes.c_uint

        self.context = self.lib.interception_create_context()
        if not self.context:
            raise RuntimeError("Failed to create Interception context")

        self._send_lock = threading.Lock()
        self.keyboard_device = self._pick_device(is_keyboard=True)
        self.mouse_device = self._pick_device(is_keyboard=False)

    @classmethod
    def get_instance(cls, dll_path: str = "interception.dll") -> "SharedInterceptionContext":
        """Get or create the singleton instance."""
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = cls(dll_path)
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Destroy the singleton (for shutdown)."""
        with cls._init_lock:
            if cls._instance is not None:
                cls._instance.close()
                cls._instance = None

    def _has_hardware(self, device: int) -> bool:
        """Return True if a real hardware device is bound to this Interception slot."""
        buf = ctypes.create_string_buffer(512)
        n = self.lib.interception_get_hardware_id(
            self.context, device, ctypes.byref(buf), ctypes.sizeof(buf))
        return n > 0

    def _pick_device(self, is_keyboard: bool) -> int:
        if is_keyboard:
            slots = range(1, 11)
            check = self.lib.interception_is_keyboard
            label = "keyboard"
        else:
            slots = range(11, 21)
            check = self.lib.interception_is_mouse
            label = "mouse"

        # Prefer a slot that has actual connected hardware — sending to an
        # empty slot causes interception_send to return 0.
        for device in slots:
            if check(device) and self._has_hardware(device):
                return device

        # Fallback: any slot of the right type (legacy behavior).
        for device in slots:
            if check(device):
                return device

        raise RuntimeError(f"No {label} device found by Interception")

    def close(self) -> None:
        if self.context:
            self.lib.interception_destroy_context(self.context)
            self.context = None

    # --- Keyboard operations (thread-safe) ---

    def send_scan(self, scan_code: int, key_up: bool = False, e0: bool = False) -> None:
        state = INTERCEPTION_KEY_UP if key_up else INTERCEPTION_KEY_DOWN
        if e0:
            state |= INTERCEPTION_KEY_E0
        stroke = InterceptionKeyStroke(code=scan_code, state=state, information=0)
        with self._send_lock:
            sent = self.lib.interception_send(
                self.context, self.keyboard_device, ctypes.byref(stroke), 1)
        if sent != 1:
            raise RuntimeError("interception_send (keyboard) failed")

    def tap_scan(self, scan_code: int, hold_sec: float = 0.03, e0: bool = False) -> None:
        self.send_scan(scan_code, key_up=False, e0=e0)
        time.sleep(max(0.0, hold_sec))
        self.send_scan(scan_code, key_up=True, e0=e0)

    # --- Mouse operations (thread-safe) ---

    def mouse_send(self, state: int, flags: int = INTERCEPTION_MOUSE_MOVE_RELATIVE,
                   rolling: int = 0, x: int = 0, y: int = 0) -> None:
        stroke = InterceptionMouseStroke(state=state, flags=flags, rolling=rolling,
                                         x=x, y=y, information=0)
        with self._send_lock:
            sent = self.lib.interception_send(
                self.context, self.mouse_device, ctypes.byref(stroke), 1)
        if sent != 1:
            raise RuntimeError("interception_send (mouse) failed")

    def mouse_click(self, button: str, hold_sec: float = 0.03) -> None:
        if button not in MOUSE_BUTTON_DOWN:
            raise ValueError(f"Unsupported mouse button: {button!r}")
        self.mouse_send(MOUSE_BUTTON_DOWN[button])
        time.sleep(max(0.0, hold_sec))
        self.mouse_send(MOUSE_BUTTON_UP[button])

    def mouse_move(self, x: int, y: int) -> None:
        self.mouse_send(state=0, x=x, y=y)

    def mouse_move_to(self, x: int, y: int) -> None:
        ctypes.windll.user32.SetCursorPos(x, y)

    def mouse_scroll(self, amount: int) -> None:
        self.mouse_send(INTERCEPTION_MOUSE_WHEEL, rolling=amount * 120)


class SharedKeyboard:
    """Keyboard interface backed by SharedInterceptionContext."""

    def __init__(self, ctx: SharedInterceptionContext) -> None:
        self._ctx = ctx

    def send_scan(self, scan_code: int, key_up: bool = False, e0: bool = False) -> None:
        self._ctx.send_scan(scan_code, key_up=key_up, e0=e0)

    def tap_scan(self, scan_code: int, hold_sec: float = 0.03, e0: bool = False) -> None:
        self._ctx.tap_scan(scan_code, hold_sec=hold_sec, e0=e0)

    def close(self) -> None:
        pass  # Lifecycle managed by SharedInterceptionContext


class SharedMouse:
    """Mouse interface backed by SharedInterceptionContext."""

    def __init__(self, ctx: SharedInterceptionContext) -> None:
        self._ctx = ctx

    def _send(self, state: int, flags: int = INTERCEPTION_MOUSE_MOVE_RELATIVE,
              rolling: int = 0, x: int = 0, y: int = 0) -> None:
        self._ctx.mouse_send(state, flags=flags, rolling=rolling, x=x, y=y)

    def click(self, button: str, hold_sec: float = 0.03) -> None:
        self._ctx.mouse_click(button, hold_sec)

    def move(self, x: int, y: int) -> None:
        self._ctx.mouse_move(x, y)

    def move_to(self, x: int, y: int) -> None:
        self._ctx.mouse_move_to(x, y)

    def scroll(self, amount: int) -> None:
        self._ctx.mouse_scroll(amount)

    def close(self) -> None:
        pass  # Lifecycle managed by SharedInterceptionContext
