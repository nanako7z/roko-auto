#!/usr/bin/env python3
"""
Interception-based periodic input runner (Windows).

This uses the Interception driver API (kernel filter driver) to send keyboard
scan-code strokes, which is closer to real device input than SendInput.

Requirements:
1) Install Interception driver on Windows.
2) Ensure interception.dll is accessible (same folder as this script or in PATH).
3) pip install pyyaml

Run:
  python interception_runner.py --config config_interception.yaml
"""

from __future__ import annotations

import argparse
import ctypes
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

import yaml


# Interception constants (from interception.h)
INTERCEPTION_KEY_DOWN = 0x00
INTERCEPTION_KEY_UP = 0x01
INTERCEPTION_KEY_E0 = 0x02
INTERCEPTION_KEY_E1 = 0x04


@dataclass
class Schedule:
    interval_sec: float
    jitter_sec: float = 0.0
    start_delay_sec: float = 0.0


class InterceptionKeyStroke(ctypes.Structure):
    _fields_ = [
        ("code", ctypes.c_ushort),
        ("state", ctypes.c_ushort),
        ("information", ctypes.c_uint),
    ]


class InterceptionKeyboard:
    def __init__(self, dll_path: str = "interception.dll") -> None:
        self.lib = ctypes.WinDLL(dll_path)

        self.lib.interception_create_context.restype = ctypes.c_void_p
        self.lib.interception_destroy_context.argtypes = [ctypes.c_void_p]

        self.lib.interception_send.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.POINTER(InterceptionKeyStroke),
            ctypes.c_uint,
        ]
        self.lib.interception_send.restype = ctypes.c_int

        self.lib.interception_is_keyboard.argtypes = [ctypes.c_int]
        self.lib.interception_is_keyboard.restype = ctypes.c_int

        self.context = self.lib.interception_create_context()
        if not self.context:
            raise RuntimeError("Failed to create Interception context")

        self.keyboard_device = self._pick_keyboard_device()

    def close(self) -> None:
        if self.context:
            self.lib.interception_destroy_context(self.context)
            self.context = None

    def _pick_keyboard_device(self) -> int:
        # Interception device id range: 1..20
        for device in range(1, 21):
            if self.lib.interception_is_keyboard(device):
                return device
        raise RuntimeError("No keyboard device found by Interception")

    def send_scan(self, scan_code: int, key_up: bool = False, e0: bool = False) -> None:
        state = INTERCEPTION_KEY_UP if key_up else INTERCEPTION_KEY_DOWN
        if e0:
            state |= INTERCEPTION_KEY_E0

        stroke = InterceptionKeyStroke(code=scan_code, state=state, information=0)
        sent = self.lib.interception_send(
            self.context,
            self.keyboard_device,
            ctypes.byref(stroke),
            1,
        )
        if sent != 1:
            raise RuntimeError("interception_send failed")

    def tap_scan(self, scan_code: int, hold_sec: float = 0.03, e0: bool = False) -> None:
        self.send_scan(scan_code, key_up=False, e0=e0)
        time.sleep(max(0.0, hold_sec))
        self.send_scan(scan_code, key_up=True, e0=e0)


# Set-1 scan codes (common keys)
KEYMAP: Dict[str, Dict[str, Any]] = {
    "tab": {"scan": 0x0F},
    "enter": {"scan": 0x1C},
    "esc": {"scan": 0x01},
    "space": {"scan": 0x39},
    "backspace": {"scan": 0x0E},
    "up": {"scan": 0x48, "e0": True},
    "down": {"scan": 0x50, "e0": True},
    "left": {"scan": 0x4B, "e0": True},
    "right": {"scan": 0x4D, "e0": True},
    "ctrl": {"scan": 0x1D},
    "shift": {"scan": 0x2A},
    "alt": {"scan": 0x38},
}

# letters a-z
for i, ch in enumerate("qwertyuiop", start=0x10):
    KEYMAP[ch] = {"scan": i}
for i, ch in enumerate("asdfghjkl", start=0x1E):
    KEYMAP[ch] = {"scan": i}
for i, ch in enumerate("zxcvbnm", start=0x2C):
    KEYMAP[ch] = {"scan": i}

# digits 0-9
for k, v in {
    "1": 0x02,
    "2": 0x03,
    "3": 0x04,
    "4": 0x05,
    "5": 0x06,
    "6": 0x07,
    "7": 0x08,
    "8": 0x09,
    "9": 0x0A,
    "0": 0x0B,
}.items():
    KEYMAP[k] = {"scan": v}


def resolve_key(key: str) -> Dict[str, Any]:
    name = key.strip().lower()
    if name not in KEYMAP:
        raise ValueError(f"Unsupported key: {key!r}")
    return KEYMAP[name]


def load_config(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def validate_schedule(data: Dict[str, Any]) -> Schedule:
    raw = data.get("schedule", {})
    interval = float(raw.get("interval_sec", 0))
    jitter = float(raw.get("jitter_sec", 0))
    start_delay = float(raw.get("start_delay_sec", 0))

    if interval <= 0:
        raise ValueError("schedule.interval_sec must be > 0")
    if jitter < 0:
        raise ValueError("schedule.jitter_sec must be >= 0")
    if start_delay < 0:
        raise ValueError("schedule.start_delay_sec must be >= 0")

    return Schedule(interval_sec=interval, jitter_sec=jitter, start_delay_sec=start_delay)


def key_down(kbd: InterceptionKeyboard, key_name: str) -> None:
    info = resolve_key(key_name)
    kbd.send_scan(info["scan"], key_up=False, e0=bool(info.get("e0", False)))


def key_up(kbd: InterceptionKeyboard, key_name: str) -> None:
    info = resolve_key(key_name)
    kbd.send_scan(info["scan"], key_up=True, e0=bool(info.get("e0", False)))


def key_tap(kbd: InterceptionKeyboard, key_name: str, hold_sec: float) -> None:
    info = resolve_key(key_name)
    kbd.tap_scan(info["scan"], hold_sec=max(0.0, hold_sec), e0=bool(info.get("e0", False)))


def execute_commands(kbd: InterceptionKeyboard, commands: List[Dict[str, Any]], default_hold_sec: float) -> None:
    for idx, cmd in enumerate(commands, start=1):
        ctype = str(cmd.get("type", "")).strip().lower()

        if ctype == "wait":
            sec = float(cmd.get("sec", 0))
            if sec < 0:
                raise ValueError(f"commands[{idx}].sec must be >= 0")
            time.sleep(sec)
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

            for k in normalized:
                key_down(kbd, k)
            time.sleep(float(cmd.get("hold_sec", default_hold_sec)))
            for k in reversed(normalized):
                key_up(kbd, k)
            continue

        raise ValueError(f"commands[{idx}] has unsupported type: {ctype!r}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Interception periodic input runner")
    parser.add_argument("--config", default="config_interception.yaml", help="Path to YAML config")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")

    cfg = load_config(config_path)
    schedule = validate_schedule(cfg)

    commands = cfg.get("commands", [])
    if not isinstance(commands, list) or not commands:
        raise ValueError("commands must be a non-empty list")

    options = cfg.get("options", {})
    default_hold_sec = float(options.get("default_hold_sec", 0.03))
    pause_between_cycles = float(options.get("pause_between_cycles_sec", 0.0))

    if default_hold_sec < 0:
        raise ValueError("options.default_hold_sec must be >= 0")
    if pause_between_cycles < 0:
        raise ValueError("options.pause_between_cycles_sec must be >= 0")

    driver = str(cfg.get("driver", {}).get("dll_path", "interception.dll"))

    if schedule.start_delay_sec > 0:
        print(f"[INFO] Starting in {schedule.start_delay_sec:.2f}s...")
        time.sleep(schedule.start_delay_sec)

    kbd = InterceptionKeyboard(dll_path=driver)
    cycle = 0

    try:
        print("[INFO] Running with Interception driver. Press Ctrl+C to stop.")
        while True:
            cycle += 1
            started_at = time.time()
            print(f"[INFO] Cycle {cycle} started")

            execute_commands(kbd, commands, default_hold_sec=default_hold_sec)

            if args.once:
                print("[INFO] --once set, exiting.")
                return

            jitter = random.uniform(-schedule.jitter_sec, schedule.jitter_sec) if schedule.jitter_sec else 0.0
            target_interval = max(0.0, schedule.interval_sec + jitter)
            elapsed = time.time() - started_at
            sleep_sec = max(0.0, target_interval - elapsed)

            if pause_between_cycles > 0:
                sleep_sec += pause_between_cycles

            print(
                f"[INFO] Cycle {cycle} done | elapsed={elapsed:.2f}s | "
                f"next_in={sleep_sec:.2f}s (target_interval={target_interval:.2f}s)"
            )
            time.sleep(sleep_sec)
    finally:
        kbd.close()


if __name__ == "__main__":
    main()
