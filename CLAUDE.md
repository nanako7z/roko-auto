# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`roko-auto` is a Windows keyboard/mouse automation tool that uses the **Interception kernel driver** to inject low-level input at the driver level (via `interception.dll`). It runs periodic cycles of configurable key/mouse sequences with randomized timing jitter. Falls back to Win32 `SendInput` if the driver is unavailable.

## Running the Tool

```bash
# Install dependency
pip install pyyaml

# Run periodic automation loop
python interception_runner.py --config config.yaml

# Run a single cycle (for config validation/testing)
python interception_runner.py --config config.yaml --once
```

**Requires Windows** with the Interception kernel driver installed (admin privileges) for driver-level injection. Without the driver, falls back to `SendInput` automatically.

## Building a Distributable Windows EXE

```bash
pip install pyarmor pyinstaller

# Obfuscate source first
pyarmor gen -O obf_dist interception_runner.py

# Package as single EXE with bundled DLL
pyinstaller --onefile --name interception_runner --add-binary "interception.dll;." obf_dist\interception_runner.py
```

Output: `dist\interception_runner.exe` — only needs `config.yaml` at runtime.

## Architecture

Everything lives in `interception_runner.py`. Key classes and their roles:

- **`InterceptionKeyboard`** — wraps `interception.dll` via `ctypes.WinDLL`. Detects keyboard device (scans IDs 1–20), exposes `send_scan()` / `tap_scan()` for raw scancode injection with E0 flag support.

- **`InterceptionMouse`** — wraps `interception.dll` for mouse input. Detects mouse device (IDs 21–40). Supports `click()`, `move()` (relative), `move_to()` (absolute normalized), `scroll()`.

- **`SendInputKeyboard` / `SendInputMouse`** — Win32 `SendInput` fallbacks used when the Interception driver fails to load. Same interface as the Interception variants. A warning is printed at startup when falling back.

- **`KEYMAP` + `resolve_key()`** — maps key name strings (e.g. `"ctrl"`, `"tab"`, `"a"`) to scan codes. All accepted key names are defined here.

- **`_human_move()`** — moves the mouse along a cubic Bézier curve with ease-in-out timing and random curvature for human-like trajectories. Used by `mouse_move` commands when `duration > 0`.

- **Command executor (`execute_commands`)** — processes six command types from config: `key`, `hotkey`, `wait`, `mouse_click`, `mouse_move`, `mouse_scroll`.

- **Scheduler** — runs cycles at `interval_sec ± jitter_sec` with an initial `start_delay_sec`. Sleep is calculated as `target_interval - elapsed + pause_between_cycles`.

- **DLL resolution (`resolve_dll_path`)** — checks `sys._MEIPASS` (PyInstaller bundle), then absolute/relative paths next to the config file, script file, and executable.

## Configuration (`config.yaml`)

```yaml
driver:
  dll_path: interception.dll

schedule:
  interval_sec: 20       # Base interval between cycles
  jitter_sec: 5          # ±random offset per cycle
  start_delay_sec: 3     # Delay before first cycle

options:
  default_hold_sec: 0.03                 # Key/click hold duration
  pause_between_cycles_sec: 0            # Extra pause after each cycle
  mouse_move_default_duration_sec: 0.8   # Default human-move duration
  mouse_move_default_wobble: 0.2         # Default Bézier curvature (0 = straight)

commands:
  - type: key
    key: tab
    # hold_sec: 0.05     # optional per-command override

  - type: wait
    sec: 2

  - type: hotkey
    keys: [ctrl, shift, esc]
    hold_sec: 0.05

  - type: mouse_click
    button: left         # left / right / middle
    # hold_sec: 0.05

  - type: mouse_move     # relative move
    x: 100
    y: -50

  - type: mouse_move     # absolute human-like move (Bézier)
    x: 960
    y: 540
    absolute: true
    duration: 0.8        # omit or 0 for instant warp
    wobble: 0.2

  - type: mouse_scroll
    amount: 3            # positive = up, negative = down
```

Supported key names: `a`–`z`, `0`–`9`, `tab`, `enter`, `esc`, `space`, `backspace`, `up`, `down`, `left`, `right`, `ctrl`, `shift`, `alt`.
