"""Recording API routes — start/stop input recording from the web UI."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .deps import app_state

router = APIRouter(prefix="/api/record", tags=["record"])


class RecordStartRequest(BaseModel):
    name: str


class _RecorderState:
    """Tracks the active recording session."""

    def __init__(self) -> None:
        self.active = False
        self.name: str = ""
        self.output_path: Optional[Path] = None
        self.thread: Optional[threading.Thread] = None
        self.event_count: int = 0
        self.started_at: float = 0
        self.error: Optional[str] = None
        self._stop_event = threading.Event()
        self._recorder: Any = None

    def reset(self) -> None:
        self.active = False
        self.name = ""
        self.output_path = None
        self.thread = None
        self.event_count = 0
        self.started_at = 0
        self.error = None
        self._stop_event.clear()
        self._recorder = None


_state = _RecorderState()


@router.get("/status")
def record_status() -> Dict[str, Any]:
    """Get current recording status."""
    return {
        "active": _state.active,
        "name": _state.name,
        "event_count": _state.event_count,
        "elapsed": round(time.time() - _state.started_at, 1) if _state.active else 0,
        "error": _state.error,
    }


@router.post("/start")
def record_start(req: RecordStartRequest) -> Dict[str, Any]:
    """Start recording input on the controlled machine."""
    if _state.active:
        raise HTTPException(status_code=409, detail="Recording already in progress")

    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Recording name is required")

    # Determine output path in commands_dir
    commands_dir = app_state.commands_dir
    if not commands_dir:
        raise HTTPException(status_code=500, detail="commands_dir not configured")
    commands_dir.mkdir(parents=True, exist_ok=True)
    bin_path = commands_dir / f"{name}.bin"

    _state.reset()
    _state.active = True
    _state.name = name
    _state.output_path = bin_path
    _state.started_at = time.time()

    _state.thread = threading.Thread(
        target=_record_worker, args=(bin_path,), daemon=True, name="recorder"
    )
    _state.thread.start()

    return {"message": f"Recording '{name}' started", "output": str(bin_path)}


@router.post("/stop")
def record_stop() -> Dict[str, Any]:
    """Stop the active recording and save to command library."""
    if not _state.active:
        raise HTTPException(status_code=409, detail="No recording in progress")

    _state._stop_event.set()

    # Wait for thread to finish (with timeout)
    if _state.thread and _state.thread.is_alive():
        _state.thread.join(timeout=5.0)

    if _state.error:
        err = _state.error
        _state.reset()
        raise HTTPException(status_code=500, detail=f"Recording failed: {err}")

    result = {
        "message": f"Recording '{_state.name}' saved",
        "name": _state.name,
        "event_count": _state.event_count,
        "elapsed": round(time.time() - _state.started_at, 1),
    }

    # Save a YAML command file that references the .bin recording
    _save_recording_command(_state.name, _state.output_path, _state.event_count)

    _state.reset()
    return result


def _record_worker(output_path: Path) -> None:
    """Background thread: run the Interception recorder."""
    import ctypes
    import struct

    try:
        from ..input.constants import (
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
        from ..input.replay import (
            _clamp_delta_ms,
            _write_rec_header,
            _write_rec_key,
            _write_rec_mouse,
        )

        # Resolve DLL path
        from ..commands.loader import resolve_dll_path
        dll_path = resolve_dll_path("interception.dll", output_path)

        lib = ctypes.WinDLL(dll_path)

        lib.interception_create_context.restype = ctypes.c_void_p
        lib.interception_destroy_context.argtypes = [ctypes.c_void_p]
        lib.interception_set_filter.argtypes = [
            ctypes.c_void_p, INTERCEPTION_PREDICATE, ctypes.c_ushort,
        ]
        lib.interception_set_filter.restype = None
        lib.interception_wait_with_timeout.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        lib.interception_wait_with_timeout.restype = ctypes.c_int
        lib.interception_receive.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint,
        ]
        lib.interception_receive.restype = ctypes.c_int
        lib.interception_send.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint,
        ]
        lib.interception_send.restype = ctypes.c_int
        lib.interception_is_keyboard.argtypes = [ctypes.c_int]
        lib.interception_is_keyboard.restype = ctypes.c_int
        lib.interception_is_mouse.argtypes = [ctypes.c_int]
        lib.interception_is_mouse.restype = ctypes.c_int

        context = lib.interception_create_context()
        if not context:
            _state.error = "Failed to create Interception context"
            _state.active = False
            return

        kb_pred = INTERCEPTION_PREDICATE(lambda d: int(1 <= d <= 10))
        mouse_pred = INTERCEPTION_PREDICATE(lambda d: int(11 <= d <= 20))
        lib.interception_set_filter(context, kb_pred, INTERCEPTION_FILTER_KEY_ALL)
        lib.interception_set_filter(context, mouse_pred, INTERCEPTION_FILTER_MOUSE_ALL)

        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            count = 0

            with output_path.open("wb") as f:
                _write_rec_header(f, 0)

                # Move cursor to origin
                ctypes.windll.user32.SetCursorPos(0, 0)
                _write_rec_mouse(f, 0, 0, INTERCEPTION_MOUSE_MOVE_ABSOLUTE, 0, 0, 0)
                count = 1
                _state.event_count = count
                print(f"[REC] Recording '{_state.name}' started. Cursor at origin.")

                last_time = time.perf_counter()
                ctrl_held = False

                while not _state._stop_event.is_set():
                    device = lib.interception_wait_with_timeout(context, 100)
                    if device == 0:
                        continue

                    now = time.perf_counter()
                    delta_ms = _clamp_delta_ms(now - last_time)
                    is_kb = 1 <= device <= 10

                    if is_kb:
                        stroke = InterceptionKeyStroke()
                        n = lib.interception_receive(context, device, ctypes.byref(stroke), 1)
                        if n <= 0:
                            continue

                        base_state = stroke.state & ~INTERCEPTION_KEY_E0
                        is_down = (base_state == INTERCEPTION_KEY_DOWN)

                        if stroke.code == 0x1D:
                            ctrl_held = is_down

                        # F12 also stops recording
                        if stroke.code == F12_SCAN and is_down:
                            print(f"[REC] F12 pressed — stopping.")
                            _state._stop_event.set()
                            lib.interception_send(context, device, ctypes.byref(stroke), 1)
                            break

                        _write_rec_key(f, delta_ms, stroke.code, stroke.state)
                        count += 1
                        _state.event_count = count
                        lib.interception_send(context, device, ctypes.byref(stroke), 1)
                    else:
                        stroke = InterceptionMouseStroke()
                        n = lib.interception_receive(context, device, ctypes.byref(stroke), 1)
                        if n <= 0:
                            continue

                        _write_rec_mouse(f, delta_ms, stroke.state,
                                         stroke.flags, stroke.rolling,
                                         stroke.x, stroke.y)
                        count += 1
                        _state.event_count = count
                        lib.interception_send(context, device, ctypes.byref(stroke), 1)

                    last_time = now

                # Update header with final count
                f.seek(0)
                _write_rec_header(f, count)

            _state.event_count = count
            print(f"[REC] Recording '{_state.name}' finished: {count} events.")

        finally:
            lib.interception_destroy_context(context)

    except Exception as e:
        _state.error = str(e)
        print(f"[REC] Recording error: {e}")
    finally:
        _state.active = False


def _save_recording_command(name: str, bin_path: Path, event_count: int) -> None:
    """Create a YAML command file in the command library that references the .bin."""
    import yaml

    commands_dir = app_state.commands_dir
    if not commands_dir:
        return

    yaml_path = commands_dir / f"{name}.yaml"
    data = {
        "source": "recording",
        "event_count": event_count,
        "commands": [
            {"type": "file", "path": bin_path.name},
        ],
    }

    with yaml_path.open("w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    print(f"[REC] Command file saved: {yaml_path}")
