"""TaskManager — orchestrates multiple task runners with CRUD and lifecycle control."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config.models import TaskConfig
from .models import TaskState, TaskStatus
from .task_runner import TaskRunner


class TaskManager:
    """Central manager for all task lifecycles."""

    def __init__(self, kbd: Any, mouse: Any, config_dir: Path = Path(".")) -> None:
        self.kbd = kbd
        self.mouse = mouse
        self.config_dir = config_dir
        self._tasks: Dict[str, TaskRunner] = {}
        self._lock = threading.RLock()

    def add_task(self, config: TaskConfig, auto_start: bool = False) -> TaskStatus:
        """Register a new task. Optionally start it immediately."""
        with self._lock:
            if config.name in self._tasks:
                raise ValueError(f"Task '{config.name}' already exists")
            runner = TaskRunner(config, self.kbd, self.mouse, self.config_dir)
            self._tasks[config.name] = runner
            if auto_start and config.enabled:
                runner.start()
            return runner.status

    def remove_task(self, name: str) -> None:
        """Stop and remove a task."""
        with self._lock:
            runner = self._get_runner(name)
            if runner.is_running:
                runner.stop()
            del self._tasks[name]

    def update_task(self, name: str, config: TaskConfig) -> TaskStatus:
        """Update a task's configuration. Stops it first if running."""
        with self._lock:
            runner = self._get_runner(name)
            was_running = runner.is_running
            if was_running:
                runner.stop()
            del self._tasks[name]

        # Re-create with new config
        new_runner = TaskRunner(config, self.kbd, self.mouse, self.config_dir)
        with self._lock:
            self._tasks[config.name] = new_runner
            if was_running and config.enabled:
                new_runner.start()
        return new_runner.status

    def start_task(self, name: str) -> TaskStatus:
        runner = self._get_runner(name)
        runner.start()
        return runner.status

    def stop_task(self, name: str) -> TaskStatus:
        runner = self._get_runner(name)
        runner.stop()
        return runner.status

    def pause_task(self, name: str) -> TaskStatus:
        runner = self._get_runner(name)
        runner.pause()
        return runner.status

    def resume_task(self, name: str) -> TaskStatus:
        runner = self._get_runner(name)
        runner.resume()
        return runner.status

    def trigger_task(self, name: str) -> TaskStatus:
        """Trigger one immediate cycle."""
        runner = self._get_runner(name)
        runner.trigger_once()
        return runner.status

    def get_task_status(self, name: str) -> TaskStatus:
        return self._get_runner(name).status

    def get_task_config(self, name: str) -> TaskConfig:
        return self._get_runner(name).config

    def list_tasks(self) -> List[TaskStatus]:
        with self._lock:
            return [r.status for r in self._tasks.values()]

    def list_task_details(self) -> List[Dict[str, Any]]:
        """List tasks with both config and status."""
        with self._lock:
            result = []
            for r in self._tasks.values():
                result.append({
                    "config": r.config.model_dump(),
                    "status": r.status.model_dump(),
                })
            return result

    def stop_all(self) -> None:
        """Stop all running tasks."""
        with self._lock:
            for runner in self._tasks.values():
                if runner.is_running:
                    runner.stop()

    def start_all_enabled(self) -> None:
        """Start all enabled tasks."""
        with self._lock:
            for runner in self._tasks.values():
                if runner.config.enabled and not runner.is_running:
                    runner.start()

    def _get_runner(self, name: str) -> TaskRunner:
        with self._lock:
            runner = self._tasks.get(name)
        if runner is None:
            raise KeyError(f"Task '{name}' not found")
        return runner
