"""Interception-based input recorder — captures keyboard and mouse events to binary files."""

from __future__ import annotations

import ctypes
import struct
import time
from pathlib import Path
from typing import Any

from .constants import (
    INTERCEPTION_FILTER_KEY_ALL,
    INTERCEPTION_FILTER_MOUSE_ALL,
    INTERCEPTION_KEY_DOWN,
    INTERCEPTION_KEY_E0,
    INTERCEPTION_MOUSE_MOVE_ABSOLUTE,
    INTERCEPTION_PREDICATE,
    InterceptionKeyStroke,
    InterceptionMouseStroke,
    F12_SCAN,
)
from .replay import (
    _REC_HEADER_FMT,
    _REC_MAGIC,
    _REC_VERSION,
    _clamp_delta_ms,
    _write_rec_header,
    _write_rec_key,
    _write_rec_mouse,
)


class InterceptionRecorder:
    """Records keyboard and mouse input via Interception driver to a binary file."""

    def __init__(self, dll_path: str = "interception.dll") -> None:
        self.lib = ctypes.WinDLL(dll_path)

        self.lib.interception_create_context.restype = ctypes.c_void_p
        self.lib.interception_destroy_context.argtypes = [ctypes.c_void_p]

        self.lib.interception_set_filter.argtypes = [
            ctypes.c_void_p, INTERCEPTION_PREDICATE, ctypes.c_ushort,
        ]
        self.lib.interception_set_filter.restype = None

        self.lib.interception_wait_with_timeout.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        self.lib.interception_wait_with_timeout.restype = ctypes.c_int

        self.lib.interception_receive.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint,
        ]
        self.lib.interception_receive.restype = ctypes.c_int

        self.lib.interception_send.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint,
        ]
        self.lib.interception_send.restype = ctypes.c_int

        self.lib.interception_is_keyboard.argtypes = [ctypes.c_int]
        self.lib.interception_is_keyboard.restype = ctypes.c_int

        self.lib.interception_is_mouse.argtypes = [ctypes.c_int]
        self.lib.interception_is_mouse.restype = ctypes.c_int

        self.context = self.lib.interception_create_context()
        if not self.context:
            raise RuntimeError("Failed to create Interception context for recording")

        self._kb_pred = INTERCEPTION_PREDICATE(lambda d: int(1 <= d <= 10))
        self._mouse_pred = INTERCEPTION_PREDICATE(lambda d: int(11 <= d <= 20))

        self.lib.interception_set_filter(self.context, self._kb_pred, INTERCEPTION_FILTER_KEY_ALL)
        self.lib.interception_set_filter(self.context, self._mouse_pred, INTERCEPTION_FILTER_MOUSE_ALL)

    def close(self) -> None:
        if self.context:
            self.lib.interception_destroy_context(self.context)
            self.context = None

    @staticmethod
    def _check_stop_hotkey() -> bool:
        user32 = ctypes.windll.user32
        if user32.GetAsyncKeyState(0x7B) & 0x8000:
            return True
        if (user32.GetAsyncKeyState(0x11) & 0x8000) and (user32.GetAsyncKeyState(0x43) & 0x8000):
            return True
        return False

    def record_loop(self, output_path: Path, mouse: Any,
                    stop_event=None, on_event=None) -> int:
        """Capture events and write to binary file. Returns event count.

        Args:
            output_path: Path to write the .bin recording.
            mouse: Mouse device instance (unused, kept for API compat).
            stop_event: Optional threading.Event — when set, stops recording.
            on_event: Optional callback(count) called after each event is recorded.
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)
        count = 0

        with output_path.open("wb") as f:
            _write_rec_header(f, 0)

            ctypes.windll.user32.SetCursorPos(0, 0)
            _write_rec_mouse(f, 0, 0, INTERCEPTION_MOUSE_MOVE_ABSOLUTE, 0, 0, 0)
            count = 1
            if on_event:
                on_event(count)
            print("[INFO] Cursor moved to origin (0, 0).")

            last_time = time.perf_counter()
            ctrl_held = False

            try:
                while True:
                    if stop_event and stop_event.is_set():
                        print("[INFO] External stop signal — stopping recording.")
                        break

                    device = self.lib.interception_wait_with_timeout(self.context, 100)

                    if device == 0:
                        if self._check_stop_hotkey():
                            print("\n[INFO] Stop hotkey detected — stopping recording.")
                            break
                        continue

                    now = time.perf_counter()
                    delta_ms = _clamp_delta_ms(now - last_time)

                    is_kb = 1 <= device <= 10

                    if is_kb:
                        stroke = InterceptionKeyStroke()
                        n = self.lib.interception_receive(
                            self.context, device, ctypes.byref(stroke), 1)
                        if n <= 0:
                            continue

                        base_state = stroke.state & ~INTERCEPTION_KEY_E0
                        is_down = (base_state == INTERCEPTION_KEY_DOWN)

                        if stroke.code == 0x1D:
                            ctrl_held = is_down

                        if stroke.code == F12_SCAN and is_down:
                            print("\n[INFO] F12 pressed — stopping recording.")
                            self.lib.interception_send(
                                self.context, device, ctypes.byref(stroke), 1)
                            break

                        if stroke.code == 0x2E and is_down and ctrl_held:
                            print("\n[INFO] Ctrl+C pressed — stopping recording.")
                            self.lib.interception_send(
                                self.context, device, ctypes.byref(stroke), 1)
                            break

                        _write_rec_key(f, delta_ms, stroke.code, stroke.state)
                        count += 1
                        if on_event:
                            on_event(count)
                        self.lib.interception_send(
                            self.context, device, ctypes.byref(stroke), 1)
                    else:
                        stroke = InterceptionMouseStroke()
                        n = self.lib.interception_receive(
                            self.context, device, ctypes.byref(stroke), 1)
                        if n <= 0:
                            continue

                        _write_rec_mouse(f, delta_ms, stroke.state,
                                         stroke.flags, stroke.rolling,
                                         stroke.x, stroke.y)
                        count += 1
                        if on_event:
                            on_event(count)
                        self.lib.interception_send(
                            self.context, device, ctypes.byref(stroke), 1)

                    last_time = now

            except KeyboardInterrupt:
                print("\n[INFO] Ctrl+C — stopping recording.")

            f.seek(0)
            _write_rec_header(f, count)

        return count
