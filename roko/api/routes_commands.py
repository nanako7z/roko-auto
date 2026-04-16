"""Command file management API routes."""

from __future__ import annotations

from typing import Any, Dict, List

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .deps import app_state

router = APIRouter(prefix="/api/commands", tags=["commands"])


class CommandFileRequest(BaseModel):
    name: str
    commands: List[Dict[str, Any]]


@router.get("")
def list_command_files() -> List[Dict[str, Any]]:
    """List all command files in commands_dir."""
    commands_dir = app_state.commands_dir
    if not commands_dir or not commands_dir.exists():
        return []
    result = []
    for ext in ("*.yaml", "*.yml"):
        for f in sorted(commands_dir.glob(ext)):
            try:
                with f.open("r", encoding="utf-8") as fh:
                    data = yaml.safe_load(fh) or {}
                cmds = data.get("commands", [])
                entry = {
                    "name": f.stem,
                    "filename": f.name,
                    "command_count": len(cmds) if isinstance(cmds, list) else 0,
                }
                if data.get("source"):
                    entry["source"] = data["source"]
                if data.get("event_count"):
                    entry["event_count"] = data["event_count"]
                result.append(entry)
            except Exception:
                result.append({"name": f.stem, "filename": f.name, "command_count": 0})
    return result


@router.get("/{name}")
def get_command_file(name: str) -> Dict[str, Any]:
    """Read a command file's content."""
    f = _resolve_file(name)
    if not f:
        raise HTTPException(status_code=404, detail=f"Command file '{name}' not found")
    with f.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return {"name": name, "filename": f.name, "commands": data.get("commands", [])}


@router.post("")
def create_command_file(req: CommandFileRequest) -> Dict[str, Any]:
    """Create a new command file."""
    commands_dir = app_state.commands_dir
    if not commands_dir:
        raise HTTPException(status_code=500, detail="commands_dir not configured")
    commands_dir.mkdir(parents=True, exist_ok=True)

    fname = req.name if req.name.endswith((".yaml", ".yml")) else req.name + ".yaml"
    f = commands_dir / fname
    if f.exists():
        raise HTTPException(status_code=409, detail=f"Command file '{fname}' already exists")

    _write_commands(f, req.commands)
    return {"name": f.stem, "filename": f.name, "message": f"Command file '{f.name}' created"}


@router.put("/{name}")
def update_command_file(name: str, req: CommandFileRequest) -> Dict[str, Any]:
    """Update an existing command file."""
    f = _resolve_file(name)
    if not f:
        raise HTTPException(status_code=404, detail=f"Command file '{name}' not found")
    _write_commands(f, req.commands)
    return {"name": f.stem, "filename": f.name, "message": f"Command file '{f.name}' updated"}


@router.delete("/{name}")
def delete_command_file(name: str) -> Dict[str, Any]:
    """Delete a command file and associated .bin recording if present."""
    f = _resolve_file(name)
    if not f:
        raise HTTPException(status_code=404, detail=f"Command file '{name}' not found")
    # Check if it's a recording and clean up .bin file
    try:
        with f.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        if data.get("source") == "recording":
            bin_path = f.parent / f"{f.stem}.bin"
            if bin_path.exists():
                bin_path.unlink()
    except Exception:
        pass
    f.unlink()
    return {"message": f"Command file '{f.name}' deleted"}


@router.post("/{name}/test")
def test_command_file(name: str) -> Dict[str, Any]:
    """Execute a command file once for testing."""
    f = _resolve_file(name)
    if not f:
        raise HTTPException(status_code=404, detail=f"Command file '{name}' not found")

    kbd = app_state.kbd
    mouse = app_state.mouse
    if not kbd or not mouse:
        raise HTTPException(status_code=503, detail="Input devices not available")

    with f.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    commands = data.get("commands", [])
    if not commands:
        raise HTTPException(status_code=400, detail="Command file has no commands")

    import threading
    from ..commands.executor import execute_commands
    from ..config.models import TaskOptions

    opts = TaskOptions()
    commands_dir = app_state.commands_dir

    def _run():
        try:
            execute_commands(
                kbd, mouse, commands,
                default_hold_sec=opts.default_hold_sec,
                mouse_move_default_duration_sec=opts.mouse_move_default_duration_sec,
                mouse_move_default_wobble=opts.mouse_move_default_wobble,
                config_dir=commands_dir or f.parent,
                commands_dir=commands_dir,
            )
            print(f"[TEST] Command '{name}' executed successfully.")
        except Exception as e:
            print(f"[TEST] Command '{name}' error: {e}")

    t = threading.Thread(target=_run, daemon=True, name=f"test-{name}")
    t.start()

    return {"message": f"Command '{name}' test started", "command_count": len(commands)}


def _resolve_file(name: str):
    """Find a command file by stem name."""
    commands_dir = app_state.commands_dir
    if not commands_dir:
        return None
    for ext in (".yaml", ".yml"):
        f = commands_dir / (name + ext)
        if f.exists():
            return f
    # Try exact filename
    f = commands_dir / name
    if f.exists():
        return f
    return None


def _write_commands(path, commands):
    """Write commands list to a YAML file."""
    data = {"commands": commands}
    with path.open("w", encoding="utf-8") as fh:
        yaml.dump(data, fh, default_flow_style=False, allow_unicode=True, sort_keys=False)
