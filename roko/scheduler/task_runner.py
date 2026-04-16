"""TaskRunner — runs a single task in its own thread."""

from __future__ import annotations

import threading
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from ..commands.executor import execute_commands
from ..commands.loader import load_config
from ..config.models import ScheduleType, TaskConfig
from .models import TaskState, TaskStatus
from .schedule_types import ScheduleCalculator


class TaskRunner:
    """Manages execution of a single task in a daemon thread."""

    def __init__(self, config: TaskConfig, kbd: Any, mouse: Any,
                 config_dir: Path = Path("."),
                 commands_dir: Path | None = None) -> None:
        self.config = config
        self.kbd = kbd
        self.mouse = mouse
        self.config_dir = config_dir
        self.commands_dir = commands_dir

        self.status = TaskStatus(name=config.name)
        self.scheduler = ScheduleCalculator(config.schedule)

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()  # Not paused by default

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.is_running:
            return
        self._stop_event.clear()
        self._pause_event.set()
        self.status.state = TaskState.running
        self.status.started_at = datetime.now()
        self.status.last_error = None
        self._thread = threading.Thread(target=self._run_loop, daemon=True,
                                        name=f"task-{self.name}")
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._pause_event.set()  # Unblock if paused
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        self.status.state = TaskState.idle
        self.status.next_run = None

    def pause(self) -> None:
        self._pause_event.clear()
        self.status.state = TaskState.paused

    def resume(self) -> None:
        self._pause_event.set()
        if self.is_running:
            self.status.state = TaskState.running

    def trigger_once(self) -> None:
        """Trigger a single immediate execution in a new thread."""
        t = threading.Thread(target=self._execute_cycle, daemon=True,
                             name=f"task-{self.name}-trigger")
        t.start()

    def _run_loop(self) -> None:
        try:
            # Initial delay
            initial = self.scheduler.initial_delay()
            if initial > 0 and not self._stop_event.is_set():
                print(f"[{self.name}] Starting in {initial:.1f}s...")
                if self._stop_event.wait(initial):
                    return

            while not self._stop_event.is_set():
                # Check pause
                self._pause_event.wait()
                if self._stop_event.is_set():
                    break

                started_at = time.time()
                self._execute_cycle()

                if self._stop_event.is_set():
                    break

                # Oneshot: run once and done
                if self.config.schedule.type == ScheduleType.oneshot:
                    self.status.state = TaskState.completed
                    print(f"[{self.name}] Oneshot task completed.")
                    return

                # Calculate next delay
                elapsed = time.time() - started_at
                delay = self.scheduler.next_delay(elapsed)
                if delay is None:
                    self.status.state = TaskState.completed
                    return

                # Add pause_between_cycles
                pause = self.config.options.pause_between_cycles_sec
                if pause > 0:
                    delay += pause

                self.status.next_run = datetime.now() + timedelta(seconds=delay)
                print(
                    f"[{self.name}] Cycle {self.status.cycle_count} done | "
                    f"elapsed={elapsed:.2f}s | next_in={delay:.2f}s"
                )

                # Interruptible sleep
                if self._stop_event.wait(delay):
                    break

        except Exception as e:
            self.status.state = TaskState.error
            self.status.last_error = traceback.format_exc()
            print(f"[{self.name}] Task error: {e}")

    def _execute_cycle(self) -> None:
        self.status.cycle_count += 1
        self.status.last_run = datetime.now()
        print(f"[{self.name}] Cycle {self.status.cycle_count} started")

        commands = self._resolve_commands()

        try:
            execute_commands(
                self.kbd,
                self.mouse,
                commands,
                default_hold_sec=self.config.options.default_hold_sec,
                mouse_move_default_duration_sec=self.config.options.mouse_move_default_duration_sec,
                mouse_move_default_wobble=self.config.options.mouse_move_default_wobble,
                config_dir=self.config_dir,
                commands_dir=self.commands_dir,
            )
        except Exception as e:
            self.status.last_error = str(e)
            print(f"[{self.name}] Command execution error: {e}")

    def _resolve_commands(self) -> list:
        """Get command list — from inline config or command_file."""
        if self.config.commands:
            return self.config.commands

        if self.config.command_file:
            file_path = Path(self.config.command_file)
            if not file_path.is_absolute():
                file_path = self.config_dir / file_path
            file_path = file_path.resolve()
            cfg = load_config(file_path)
            commands = cfg.get("commands", [])
            if not isinstance(commands, list) or not commands:
                raise ValueError(f"Command file {file_path} has no valid 'commands' list")
            return commands

        raise ValueError(f"Task '{self.name}' has no commands or command_file")
