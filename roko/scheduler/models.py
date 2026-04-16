"""Runtime models for task status tracking."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel


class TaskState(str, Enum):
    idle = "idle"
    running = "running"
    paused = "paused"
    error = "error"
    completed = "completed"


class TaskStatus(BaseModel):
    name: str
    state: TaskState = TaskState.idle
    cycle_count: int = 0
    last_run: Optional[datetime] = None
    next_run: Optional[datetime] = None
    last_error: Optional[str] = None
    started_at: Optional[datetime] = None
