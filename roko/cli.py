"""CLI entry point with subcommands: serve, run, record."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path


def cmd_serve(args: argparse.Namespace) -> None:
    """Start the web server with task scheduler."""
    from .server import start_server

    config_path = Path(args.config).resolve() if args.config else None
    start_server(config_path)


def cmd_run(args: argparse.Namespace) -> None:
    """Run a single task (no web server) — supports both new and legacy config formats."""
    from .commands.executor import execute_commands
    from .commands.loader import resolve_dll_path
    from .config.loader import load_task_config, load_yaml

    if args.task:
        task_path = Path(args.task).resolve()
    elif args.config:
        task_path = Path(args.config).resolve()
    else:
        task_path = Path("config.yaml").resolve()

    if not task_path.exists():
        print(f"[ERROR] Config not found: {task_path}")
        sys.exit(1)

    task_config = load_task_config(task_path)
    config_dir = task_path.parent

    # Resolve driver
    raw_data = load_yaml(task_path)
    dll_path = raw_data.get("driver", {}).get("dll_path", "interception.dll")
    resolved_driver = resolve_dll_path(dll_path, task_path)

    # Init input devices
    try:
        from .input.context import SharedInterceptionContext, SharedKeyboard, SharedMouse
        ctx = SharedInterceptionContext.get_instance(resolved_driver)
        kbd = SharedKeyboard(ctx)
        mouse = SharedMouse(ctx)
        print("[INFO] Interception driver active.")
    except (RuntimeError, OSError) as exc:
        print(f"[WARN] Interception unavailable: {exc}")
        print("[WARN] Falling back to Win32 SendInput.")
        from .input.keyboard import SendInputKeyboard
        from .input.mouse import SendInputMouse
        kbd = SendInputKeyboard()
        mouse = SendInputMouse()

    # Resolve commands
    if task_config.commands:
        commands = task_config.commands
    elif task_config.command_file:
        from .commands.loader import load_config
        cf_path = Path(task_config.command_file)
        if not cf_path.is_absolute():
            cf_path = config_dir / cf_path
        cf_data = load_config(cf_path.resolve())
        commands = cf_data.get("commands", [])
    else:
        print("[ERROR] No commands defined")
        sys.exit(1)

    opts = task_config.options
    schedule = task_config.schedule

    if schedule.start_delay_sec > 0:
        print(f"[INFO] Starting in {schedule.start_delay_sec:.2f}s...")
        time.sleep(schedule.start_delay_sec)

    import random
    cycle = 0

    try:
        print("[INFO] Running. Press Ctrl+C to stop.")
        while True:
            cycle += 1
            started_at = time.time()
            print(f"[INFO] Cycle {cycle} started")

            execute_commands(
                kbd, mouse, commands,
                default_hold_sec=opts.default_hold_sec,
                mouse_move_default_duration_sec=opts.mouse_move_default_duration_sec,
                mouse_move_default_wobble=opts.mouse_move_default_wobble,
                config_dir=config_dir,
            )

            if args.once:
                print("[INFO] --once set, exiting.")
                return

            interval = schedule.interval_sec or 20.0
            jitter = 0.0
            if schedule.jitter_sec > 0:
                jitter = random.uniform(-schedule.jitter_sec, schedule.jitter_sec)
            target_interval = max(0.0, interval + jitter)
            elapsed = time.time() - started_at
            sleep_sec = max(0.0, target_interval - elapsed)

            if opts.pause_between_cycles_sec > 0:
                sleep_sec += opts.pause_between_cycles_sec

            print(
                f"[INFO] Cycle {cycle} done | elapsed={elapsed:.2f}s | "
                f"next_in={sleep_sec:.2f}s (target_interval={target_interval:.2f}s)"
            )
            time.sleep(sleep_sec)
    except KeyboardInterrupt:
        print("\n[INFO] Stopped by user.")
    finally:
        kbd.close()
        mouse.close()


def cmd_record(args: argparse.Namespace) -> None:
    """Record keyboard/mouse input to a .bin file."""
    from .commands.loader import resolve_dll_path
    from .config.loader import load_yaml
    from .input.mouse import InterceptionMouse
    from .input.recorder import InterceptionRecorder

    config_path = Path(args.config).resolve() if args.config else Path("config.yaml").resolve()
    cfg = load_yaml(config_path) if config_path.exists() else {}
    dll_path = cfg.get("driver", {}).get("dll_path", "interception.dll")
    resolved_driver = resolve_dll_path(dll_path, config_path)

    try:
        recorder = InterceptionRecorder(dll_path=resolved_driver)
    except (RuntimeError, OSError) as exc:
        print(f"[ERROR] Recording requires the Interception driver: {exc}")
        sys.exit(1)

    try:
        mouse = InterceptionMouse(dll_path=resolved_driver)
    except (RuntimeError, OSError) as exc:
        print(f"[ERROR] Recording requires the Interception mouse driver: {exc}")
        recorder.close()
        sys.exit(1)

    output_path = Path(args.output)
    print(f"[INFO] Recording to {output_path}. Press F12 to stop (Ctrl+C as backup).")
    try:
        count = recorder.record_loop(output_path, mouse)
    finally:
        recorder.close()
        mouse.close()
    print(f"[INFO] Recorded {count} events to {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="roko",
        description="roko-auto — multi-task input automation platform",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # serve
    serve_parser = subparsers.add_parser("serve", help="Start web server with task scheduler")
    serve_parser.add_argument("--config", default=None,
                              help="Path to server.yaml (default: auto-detect)")

    # run
    run_parser = subparsers.add_parser("run", help="Run a single task (no web server)")
    run_parser.add_argument("--task", default=None, help="Path to task YAML file")
    run_parser.add_argument("--config", default=None, help="Path to legacy config.yaml")
    run_parser.add_argument("--once", action="store_true", help="Run one cycle and exit")

    # record
    record_parser = subparsers.add_parser("record", help="Record input to .bin file")
    record_parser.add_argument("output", help="Output .bin file path")
    record_parser.add_argument("--config", default=None, help="Config for driver DLL path")

    args = parser.parse_args()

    if args.command == "serve":
        cmd_serve(args)
    elif args.command == "run":
        cmd_run(args)
    elif args.command == "record":
        cmd_record(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
