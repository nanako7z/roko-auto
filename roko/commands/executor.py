"""Command execution engine — processes command sequences."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List

from ..input.helpers import resolve_key, _human_move
from ..input.replay import replay_recording
from ..input.constants import MAX_FILE_INCLUDE_DEPTH


def key_down(kbd: Any, key_name: str) -> None:
    info = resolve_key(key_name)
    kbd.send_scan(info["scan"], key_up=False, e0=bool(info.get("e0", False)))


def key_up(kbd: Any, key_name: str) -> None:
    info = resolve_key(key_name)
    kbd.send_scan(info["scan"], key_up=True, e0=bool(info.get("e0", False)))


def key_tap(kbd: Any, key_name: str, hold_sec: float) -> None:
    info = resolve_key(key_name)
    kbd.tap_scan(info["scan"], hold_sec=max(0.0, hold_sec), e0=bool(info.get("e0", False)))


def execute_commands(
    kbd: Any,
    mouse: Any,
    commands: List[Dict[str, Any]],
    default_hold_sec: float,
    mouse_move_default_duration_sec: float,
    mouse_move_default_wobble: float,
    config_dir: Path = Path("."),
    _depth: int = 0,
    _seen_files: set = None,
) -> None:
    from .loader import load_config

    for idx, cmd in enumerate(commands, start=1):
        ctype = str(cmd.get("type", "")).strip().lower()

        if ctype == "wait":
            sec = float(cmd.get("sec", 0))
            if sec < 0:
                raise ValueError(f"commands[{idx}].sec must be >= 0")
            time.sleep(sec)
            continue

        if ctype == "file":
            if _depth >= MAX_FILE_INCLUDE_DEPTH:
                raise ValueError(f"commands[{idx}]: nested 'file' includes exceed max depth {MAX_FILE_INCLUDE_DEPTH}")
            raw_path = str(cmd.get("path", "")).strip()
            if not raw_path:
                raise ValueError(f"commands[{idx}].path is required for type: file")
            file_path = Path(raw_path)
            if not file_path.is_absolute():
                file_path = config_dir / file_path
            file_path = file_path.resolve()
            if _seen_files is None:
                _seen_files = set()
            if file_path in _seen_files:
                raise ValueError(f"commands[{idx}]: circular include detected: {file_path}")
            if not file_path.exists():
                raise FileNotFoundError(f"commands[{idx}]: file not found: {file_path}")
            seen = _seen_files | {file_path}
            ext = file_path.suffix.lower()
            if ext in (".yaml", ".yml"):
                sub_cfg = load_config(file_path)
                sub_commands = sub_cfg.get("commands", [])
                if not isinstance(sub_commands, list) or not sub_commands:
                    raise ValueError(f"commands[{idx}]: file {file_path} has no valid 'commands' list")
                execute_commands(
                    kbd, mouse, sub_commands,
                    default_hold_sec=default_hold_sec,
                    mouse_move_default_duration_sec=mouse_move_default_duration_sec,
                    mouse_move_default_wobble=mouse_move_default_wobble,
                    config_dir=file_path.parent,
                    _depth=_depth + 1,
                    _seen_files=seen,
                )
            elif ext == ".bin":
                replay_recording(kbd, mouse, file_path)
            else:
                raise ValueError(f"commands[{idx}]: unsupported file type: {ext!r} (use .yaml/.yml or .bin)")
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
