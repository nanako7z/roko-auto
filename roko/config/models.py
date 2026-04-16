"""Pydantic models for server and task configuration."""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class ScheduleType(str, Enum):
    interval = "interval"
    cron = "cron"
    oneshot = "oneshot"
    sentinel = "sentinel"


class ScheduleConfig(BaseModel):
    type: ScheduleType = ScheduleType.interval
    interval_sec: Optional[float] = None
    jitter_sec: float = 0.0
    start_delay_sec: float = 0.0
    cron_expression: Optional[str] = None
    # For oneshot: if None, runs immediately
    run_at: Optional[str] = None

    @field_validator("interval_sec")
    @classmethod
    def interval_positive(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and v <= 0:
            raise ValueError("interval_sec must be > 0")
        return v

    @field_validator("jitter_sec")
    @classmethod
    def jitter_non_negative(cls, v: float) -> float:
        if v < 0:
            raise ValueError("jitter_sec must be >= 0")
        return v

    @field_validator("start_delay_sec")
    @classmethod
    def delay_non_negative(cls, v: float) -> float:
        if v < 0:
            raise ValueError("start_delay_sec must be >= 0")
        return v


class SentinelConfig(BaseModel):
    template_image: str  # Template image filename (in templates/ directory)
    scan_interval_ms: int = 1000  # Scan interval in milliseconds
    match_threshold: float = 0.8  # Match confidence threshold 0.0-1.0
    scan_region: Optional[List[int]] = None  # Optional [left, top, width, height]

    @field_validator("scan_interval_ms")
    @classmethod
    def interval_min(cls, v: int) -> int:
        if v < 100:
            raise ValueError("scan_interval_ms must be >= 100")
        return v

    @field_validator("match_threshold")
    @classmethod
    def threshold_range(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("match_threshold must be between 0.0 and 1.0")
        return v

    @field_validator("scan_region")
    @classmethod
    def region_valid(cls, v: Optional[List[int]]) -> Optional[List[int]]:
        if v is not None:
            if len(v) != 4:
                raise ValueError("scan_region must have exactly 4 elements [left, top, width, height]")
            if v[2] <= 0 or v[3] <= 0:
                raise ValueError("scan_region width and height must be > 0")
        return v


class TaskOptions(BaseModel):
    default_hold_sec: float = 0.1
    pause_between_cycles_sec: float = 0.0
    mouse_move_default_duration_sec: float = 0.8
    mouse_move_default_wobble: float = 0.1
    compensate_queue_wait: bool = True  # Subtract queue wait time from next interval

    @field_validator("default_hold_sec", "pause_between_cycles_sec",
                     "mouse_move_default_duration_sec", "mouse_move_default_wobble")
    @classmethod
    def non_negative(cls, v: float) -> float:
        if v < 0:
            raise ValueError("value must be >= 0")
        return v


class TaskConfig(BaseModel):
    name: str
    schedule: ScheduleConfig
    options: TaskOptions = Field(default_factory=TaskOptions)
    commands: List[Dict[str, Any]] = Field(default_factory=list)
    command_file: Optional[str] = None
    sentinel: Optional[SentinelConfig] = None

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("task name must not be empty")
        return v

    # Sentinel-only command types
    _SENTINEL_ONLY_COMMANDS = {"move_to_match"}

    @model_validator(mode="after")
    def check_sentinel_commands(self) -> "TaskConfig":
        """Non-sentinel tasks must not use sentinel-only commands."""
        is_sentinel = self.schedule.type == ScheduleType.sentinel
        if is_sentinel:
            return self
        for idx, cmd in enumerate(self.commands, start=1):
            ctype = str(cmd.get("type", "")).strip().lower()
            if ctype in self._SENTINEL_ONLY_COMMANDS:
                raise ValueError(
                    f"commands[{idx}]: '{ctype}' can only be used in sentinel tasks"
                )
        return self

    def has_commands(self) -> bool:
        return bool(self.commands) or bool(self.command_file)


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8642


class DriverConfig(BaseModel):
    dll_path: str = "interception.dll"


class ScreenConfig(BaseModel):
    capture_method: str = "mss"
    max_fps: int = 2


class AppConfig(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    driver: DriverConfig = Field(default_factory=DriverConfig)
    screen: ScreenConfig = Field(default_factory=ScreenConfig)
    tasks_dir: str = "./tasks"
    commands_dir: str = "./commands"
    templates_dir: str = "./templates"
