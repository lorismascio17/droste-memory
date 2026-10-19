"""Writable defaults shared by the CLI, MCP server and visualizer."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def default_db_path() -> Path:
    override = os.environ.get("DROSTE_DB")
    if override:
        return Path(override).expanduser().resolve()
    # Preserve databases in source checkouts. Installed packages must never
    # write into site-packages (which may be shared or read-only).
    checkout = Path(__file__).resolve().parents[1]
    if (checkout / "pyproject.toml").is_file() and (checkout / "core").is_dir():
        return checkout / "droste_memory_db.json"
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "Droste"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "Droste"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / "droste"
    return base / "droste_memory_db.json"
