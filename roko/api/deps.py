"""FastAPI dependency injection — shared instances for routes."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from ..scheduler.task_manager import TaskManager
from ..screen.capture import ScreenCapture


class AppState:
    """Holds shared application state for dependency injection."""

    def __init__(self) -> None:
        self.task_manager: Optional[TaskManager] = None
        self.screen_capture: Optional[ScreenCapture] = None
        self.kbd: Any = None
        self.mouse: Any = None
        self.driver_type: str = "unknown"  # "interception" or "sendinput"
        self.started_at: datetime = datetime.now()
        self.config_dir: Path = Path(".")
        self.commands_dir: Optional[Path] = None
        self.dll_path: Optional[str] = None  # Resolved Interception DLL path


# Global singleton
app_state = AppState()
