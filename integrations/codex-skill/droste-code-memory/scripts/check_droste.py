"""Check whether Droste is available for an agent workflow."""

from __future__ import annotations

import json
import shutil
import subprocess


def run(command: list[str]) -> dict:
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return {
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def main() -> None:
    executable = shutil.which("droste")
    report = {
        "droste_on_path": bool(executable),
        "executable": executable,
        "version": run(["droste", "--version"]) if executable else None,
        "status": run(["droste", "status", "--json"]) if executable else None,
        "install_hint": "python -m pip install --upgrade droste-memory",
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
