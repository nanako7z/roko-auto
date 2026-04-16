"""Server startup — initializes driver, task manager, and runs uvicorn."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import uvicorn

from .api.app import create_app
from .api.deps import app_state
from .commands.loader import resolve_dll_path
from .config.loader import load_server_config, load_tasks_from_directory
from .config.models import AppConfig
from .scheduler.task_manager import TaskManager
from .screen.capture import ScreenCapture


def _init_input_devices(dll_path: str, config_path: Path):
    """Initialize keyboard and mouse — try Interception, fall back to SendInput."""
    resolved = resolve_dll_path(dll_path, config_path)
    app_state.dll_path = resolved

    try:
        from .input.context import SharedInterceptionContext, SharedKeyboard, SharedMouse
        ctx = SharedInterceptionContext.get_instance(resolved)
        kbd = SharedKeyboard(ctx)
        mouse = SharedMouse(ctx)
        driver_type = "interception"
        print(f"[INFO] Interception driver active (DLL: {resolved})")
    except (RuntimeError, OSError) as exc:
        print("=" * 60)
        print(f"[WARN] Interception driver unavailable: {exc}")
        print("[WARN] Falling back to Win32 SendInput.")
        print("=" * 60)
        from .input.keyboard import SendInputKeyboard
        from .input.mouse import SendInputMouse
        kbd = SendInputKeyboard()
        mouse = SendInputMouse()
        driver_type = "sendinput"

    return kbd, mouse, driver_type


def start_server(config_path: Path = None) -> None:
    """Start the roko-auto web server."""

    # Load server config
    if config_path and config_path.exists():
        cfg = load_server_config(config_path)
    else:
        cfg = AppConfig()

    config_dir = config_path.parent if config_path else Path(".")

    # Init input devices
    kbd, mouse, driver_type = _init_input_devices(cfg.driver.dll_path, config_dir / "dummy")

    # Init screen capture
    try:
        screen_capture = ScreenCapture(max_fps=cfg.screen.max_fps)
        print("[INFO] Screen capture initialized.")
    except Exception as e:
        print(f"[WARN] Screen capture unavailable: {e}")
        screen_capture = None

    # Init task manager
    tasks_dir = Path(cfg.tasks_dir)
    if not tasks_dir.is_absolute():
        tasks_dir = config_dir / tasks_dir
    commands_dir = Path(cfg.commands_dir)
    if not commands_dir.is_absolute():
        commands_dir = config_dir / commands_dir
    commands_dir.mkdir(parents=True, exist_ok=True)
    task_manager = TaskManager(kbd, mouse, config_dir=config_dir, tasks_dir=tasks_dir,
                               commands_dir=commands_dir)

    # Scan command files from commands_dir
    cmd_count = 0
    if commands_dir.exists():
        for ext in ("*.yaml", "*.yml"):
            for p in sorted(commands_dir.glob(ext)):
                print(f"[INFO] Found command file: {p.stem}")
                cmd_count += 1
    print(f"[INFO] Command files discovered: {cmd_count}")

    # Load tasks from directory
    task_configs = load_tasks_from_directory(tasks_dir)
    for tc in task_configs:
        try:
            task_manager.add_task(tc)
            print(f"[INFO] Loaded task: {tc.name}")
        except Exception as e:
            print(f"[WARN] Failed to add task '{tc.name}': {e}")

    # Set up app state for dependency injection
    app_state.task_manager = task_manager
    app_state.screen_capture = screen_capture
    app_state.kbd = kbd
    app_state.mouse = mouse
    app_state.driver_type = driver_type
    app_state.started_at = datetime.now()
    app_state.config_dir = config_dir
    app_state.commands_dir = commands_dir

    app = create_app()

    print(f"[INFO] Starting server on {cfg.server.host}:{cfg.server.port}")
    print(f"[INFO] API docs: http://localhost:{cfg.server.port}/docs")
    print(f"[INFO] Tasks loaded: {len(task_configs)}")

    try:
        uvicorn.run(app, host=cfg.server.host, port=cfg.server.port,
                    log_level="info")
    finally:
        task_manager.stop_all()
        from .input.context import SharedInterceptionContext
        SharedInterceptionContext.reset()
        if hasattr(kbd, "close"):
            kbd.close()
        if hasattr(mouse, "close"):
            mouse.close()
