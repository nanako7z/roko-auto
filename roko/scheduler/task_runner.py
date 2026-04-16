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
                 commands_dir: Path | None = None,
                 exec_lock: threading.Lock | None = None,
                 screen_capture: Any = None,
                 templates_dir: Path | None = None) -> None:
        self.config = config
        self.kbd = kbd
        self.mouse = mouse
        self.config_dir = config_dir
        self.commands_dir = commands_dir
        self._exec_lock = exec_lock
        self.screen_capture = screen_capture
        self.templates_dir = templates_dir

        self.status = TaskStatus(name=config.name)
        self.scheduler = ScheduleCalculator(config.schedule)
        self._last_match: Any = None  # Stores MatchResult for sentinel tasks

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
        def _safe_execute():
            try:
                self._execute_cycle()
            except Exception as e:
                self.status.last_error = str(e)
                print(f"[{self.name}] Trigger error: {e}")

        t = threading.Thread(target=_safe_execute, daemon=True,
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

            # Sentinel tasks use a separate scan-match-execute loop
            if self.config.schedule.type == ScheduleType.sentinel:
                self._sentinel_loop()
                return

            while not self._stop_event.is_set():
                # Check pause
                self._pause_event.wait()
                if self._stop_event.is_set():
                    break

                started_at = time.time()
                wait_time = self._execute_cycle()

                if self._stop_event.is_set():
                    break

                # Oneshot: run once and done
                if self.config.schedule.type == ScheduleType.oneshot:
                    self.status.state = TaskState.completed
                    print(f"[{self.name}] Oneshot task completed.")
                    return

                # Calculate next delay
                elapsed = time.time() - started_at
                if not self.config.options.compensate_queue_wait:
                    elapsed -= wait_time
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

    def _sentinel_loop(self) -> None:
        """Sentinel task loop: periodically scan screen for template match."""
        from ..screen.matcher import TemplateMatcher

        sentinel_cfg = self.config.sentinel
        if not sentinel_cfg:
            raise ValueError(f"Task '{self.name}' is sentinel type but has no sentinel config")
        if not self.screen_capture:
            raise RuntimeError(f"Task '{self.name}': screen capture not available for sentinel task")
        if not self.templates_dir:
            raise RuntimeError(f"Task '{self.name}': templates_dir not configured")

        template_path = self.templates_dir / sentinel_cfg.template_image
        if not template_path.exists():
            # Try adding extensions
            for ext in (".png", ".jpg", ".jpeg"):
                candidate = self.templates_dir / (sentinel_cfg.template_image + ext)
                if candidate.exists():
                    template_path = candidate
                    break
        if not template_path.exists():
            raise FileNotFoundError(
                f"Template image not found: {sentinel_cfg.template_image} in {self.templates_dir}"
            )

        matcher = TemplateMatcher(template_path, threshold=sentinel_cfg.match_threshold)
        scan_delay = sentinel_cfg.scan_interval_ms / 1000.0
        region = tuple(sentinel_cfg.scan_region) if sentinel_cfg.scan_region else None

        # Track last triggered position to avoid re-triggering at the same spot.
        # Once a match triggers, subsequent matches at the same location are ignored
        # until the pattern disappears (no match) and reappears.
        triggered_pos = None  # (center_x, center_y) or None
        # Tolerance: match within half-template-size counts as "same position"
        pos_tolerance = max(matcher._w, matcher._h) // 2

        print(f"[{self.name}] Sentinel active: template={template_path.name}, "
              f"threshold={sentinel_cfg.match_threshold}, interval={sentinel_cfg.scan_interval_ms}ms")

        while not self._stop_event.is_set():
            self._pause_event.wait()
            if self._stop_event.is_set():
                break

            try:
                screenshot = self.screen_capture.capture(region=region, format="png")
                result = matcher.match(screenshot)
            except Exception as e:
                self.status.last_error = f"Scan error: {e}"
                print(f"[{self.name}] Scan error: {e}")
                if self._stop_event.wait(scan_delay):
                    break
                continue

            if result:
                # Check if this is the same position as last trigger
                same_pos = False
                if triggered_pos is not None:
                    dx = abs(result.center_x - triggered_pos[0])
                    dy = abs(result.center_y - triggered_pos[1])
                    same_pos = dx <= pos_tolerance and dy <= pos_tolerance

                if same_pos:
                    # Still at same position — skip
                    pass
                else:
                    print(f"[{self.name}] Match found! confidence={result.confidence:.3f} "
                          f"center=({result.center_x},{result.center_y})")
                    triggered_pos = (result.center_x, result.center_y)
                    self._last_match = result
                    self._execute_cycle()
                    self._last_match = None
            else:
                # Pattern disappeared — reset so it can trigger again
                if triggered_pos is not None:
                    print(f"[{self.name}] Pattern disappeared, ready to re-trigger")
                    triggered_pos = None

            if self._stop_event.wait(scan_delay):
                break

    def _execute_cycle(self) -> float:
        """Execute one cycle. Returns seconds spent waiting for the exec lock."""
        self.status.cycle_count += 1
        self.status.last_run = datetime.now()
        print(f"[{self.name}] Cycle {self.status.cycle_count} started")

        commands = self._resolve_commands()

        lock = self._exec_lock
        wait_time = 0.0
        if lock:
            t0 = time.time()
            acquired = lock.acquire(timeout=300)
            wait_time = time.time() - t0
            if not acquired:
                msg = f"Exec lock timeout after {wait_time:.0f}s — skipping cycle"
                self.status.last_error = msg
                print(f"[{self.name}] {msg}")
                return wait_time
            if wait_time > 0.01:
                print(f"[{self.name}] Waited {wait_time:.2f}s for exec lock")
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
                match_result=self._last_match,
            )
        except Exception as e:
            self.status.last_error = str(e)
            print(f"[{self.name}] Command execution error: {e}")
        finally:
            if lock:
                lock.release()
        return wait_time

    def _resolve_commands(self) -> list:
        """Get command list — from inline config or command_file."""
        if self.config.commands:
            return self.config.commands

        if self.config.command_file:
            file_path = Path(self.config.command_file)
            if not file_path.is_absolute():
                file_path = self.config_dir / file_path
                # Fallback: try commands_dir if not found in config_dir
                if not file_path.exists() and self.commands_dir:
                    alt = self.commands_dir / self.config.command_file
                    if alt.exists():
                        file_path = alt
            file_path = file_path.resolve()
            cfg = load_config(file_path)
            commands = cfg.get("commands", [])
            if not isinstance(commands, list) or not commands:
                raise ValueError(f"Command file {file_path} has no valid 'commands' list")
            return commands

        raise ValueError(f"Task '{self.name}' has no commands or command_file")
