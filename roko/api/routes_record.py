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

    def reset(self) -> None:
        self.active = False
        self.name = ""
        self.output_path = None
        self.thread = None
        self.event_count = 0
        self.started_at = 0
        self.error = None
        self._stop_event.clear()


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

    if app_state.driver_type != "interception":
        raise HTTPException(
            status_code=503,
            detail="Recording requires the Interception driver (current: "
                   f"{app_state.driver_type})",
        )

    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Recording name is required")

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

    _save_recording_command(_state.name, _state.output_path, _state.event_count)

    _state.reset()
    return result


def _record_worker(output_path: Path) -> None:
    """Background thread: use InterceptionRecorder to capture input."""
    recorder = None
    try:
        from ..input.recorder import InterceptionRecorder

        dll_path = app_state.dll_path or "interception.dll"
        recorder = InterceptionRecorder(dll_path=dll_path)

        def _on_event(count: int) -> None:
            _state.event_count = count

        print(f"[REC] Recording '{_state.name}' starting...")
        count = recorder.record_loop(
            output_path,
            mouse=None,
            stop_event=_state._stop_event,
            on_event=_on_event,
        )
        _state.event_count = count
        print(f"[REC] Recording '{_state.name}' finished: {count} events.")

    except Exception as e:
        _state.error = str(e)
        print(f"[REC] Recording error: {e}")
    finally:
        if recorder:
            recorder.close()
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
