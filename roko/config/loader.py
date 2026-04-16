"""Load and validate configuration files — server config, tasks, and legacy format migration."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .models import AppConfig, ScheduleConfig, ScheduleType, TaskConfig, TaskOptions


class _SafeEnumLoader(yaml.SafeLoader):
    """SafeLoader that handles !!python/object/apply tags for known enums
    instead of raising an error. This allows loading YAML files that were
    saved with Pydantic model_dump() enum instances."""
    pass


def _apply_constructor(loader, node):
    """Convert !!python/object/apply:... sequences back to plain strings."""
    args = loader.construct_sequence(node)
    return args[0] if args else None


# Register for any !!python/object/apply tag
_SafeEnumLoader.add_multi_constructor(
    "tag:yaml.org,2002:python/object/apply:",
    lambda loader, suffix, node: _apply_constructor(loader, node),
)


def load_yaml(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.load(f, Loader=_SafeEnumLoader) or {}


def load_server_config(path: Path) -> AppConfig:
    """Load server.yaml into AppConfig."""
    data = load_yaml(path)
    return AppConfig(**data)


def load_task_config(path: Path, auto_fix: bool = True) -> TaskConfig:
    """Load a task YAML file into TaskConfig.

    If auto_fix is True, re-saves the file in clean format when it contained
    Python-tagged YAML (e.g. !!python/object/apply enum tags).
    """
    raw_text = path.read_text(encoding="utf-8")
    data = yaml.load(raw_text, Loader=_SafeEnumLoader) or {}

    # If it looks like a legacy config (has top-level schedule+commands, no name),
    # migrate it automatically.
    if "name" not in data and "schedule" in data and "commands" in data:
        return migrate_legacy_config(data, path)

    config = TaskConfig(**data)

    # Auto-fix: re-save if the file contained Python-specific YAML tags
    if auto_fix and "!!python/" in raw_text:
        try:
            clean_data = config.model_dump(mode="json", exclude_none=True)
            with path.open("w", encoding="utf-8") as f:
                yaml.dump(clean_data, f, default_flow_style=False,
                          allow_unicode=True, sort_keys=False)
            print(f"[INFO] Auto-fixed task file: {path}")
        except Exception:
            pass  # Non-critical, loading already succeeded

    return config


def load_tasks_from_directory(tasks_dir: Path) -> List[TaskConfig]:
    """Scan a directory for task YAML files and load them all."""
    tasks = []
    if not tasks_dir.exists():
        return tasks
    for p in sorted(tasks_dir.glob("*.yaml")) + sorted(tasks_dir.glob("*.yml")):
        try:
            task = load_task_config(p)
            tasks.append(task)
        except Exception as e:
            print(f"[WARN] Failed to load task {p}: {e}")
    return tasks


def migrate_legacy_config(data: Dict[str, Any], config_path: Path) -> TaskConfig:
    """Convert a legacy config.yaml (single schedule+commands) into a TaskConfig."""
    name = config_path.stem  # e.g. "config" from "config.yaml"

    raw_schedule = data.get("schedule", {})
    schedule = ScheduleConfig(
        type=ScheduleType.interval,
        interval_sec=float(raw_schedule.get("interval_sec", 20)),
        jitter_sec=float(raw_schedule.get("jitter_sec", 0)),
        start_delay_sec=float(raw_schedule.get("start_delay_sec", 0)),
    )

    raw_options = data.get("options", {})
    options = TaskOptions(
        default_hold_sec=float(raw_options.get("default_hold_sec", 0.03)),
        pause_between_cycles_sec=float(raw_options.get("pause_between_cycles_sec", 0.0)),
        mouse_move_default_duration_sec=float(raw_options.get("mouse_move_default_duration_sec", 0)),
        mouse_move_default_wobble=float(raw_options.get("mouse_move_default_wobble", 0.2)),
    )

    commands = data.get("commands", [])

    return TaskConfig(
        name=name,
        schedule=schedule,
        options=options,
        commands=commands,
    )
