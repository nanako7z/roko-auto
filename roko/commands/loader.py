"""Configuration loading and DLL path resolution."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List

import yaml


def load_config(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def resolve_dll_path(dll_path: str, config_path: Path) -> str:
    dll_name = Path(dll_path).name
    candidates: List[Path] = []

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / dll_name)

    raw = Path(dll_path)
    if raw.is_absolute():
        candidates.append(raw)
    else:
        candidates.extend(
            [
                config_path.parent / raw,
                Path(__file__).resolve().parent.parent.parent / raw,  # project root
                Path(sys.executable).resolve().parent / raw,
                raw,
            ]
        )

    for p in candidates:
        if p.exists():
            return str(p)

    return dll_path
