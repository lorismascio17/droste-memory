"""Convenience launcher for the local Droste-Memory visualizer."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser(description="Start Droste-Memory locally.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument(
        "--with-mcp",
        action="store_true",
        help="Also start server.py as a background stdio MCP smoke process.",
    )
    args = parser.parse_args()

    env = os.environ.copy()
    pythonpath_entries = [str(ROOT)]
    local_packages = ROOT / ".python-packages"
    if local_packages.exists():
        pythonpath_entries.insert(0, str(local_packages))
    if env.get("PYTHONPATH"):
        pythonpath_entries.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_entries)
    processes: list[subprocess.Popen[bytes]] = []

    if args.with_mcp:
        processes.append(
            subprocess.Popen(
                [sys.executable, str(ROOT / "server.py")],
                cwd=ROOT,
                env=env,
                stdin=subprocess.DEVNULL,
            )
        )
        print("MCP smoke process started. Claude Desktop should still launch server.py directly.")

    url = f"http://{args.host}:{args.port}"
    print(f"Droste-Memory visualizer: {url}")
    print("Press Ctrl+C to stop.")

    processes.append(
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "visualizer.app:app",
                "--host",
                args.host,
                "--port",
                str(args.port),
            ],
            cwd=ROOT,
            env=env,
        )
    )

    try:
        return processes[-1].wait()
    except KeyboardInterrupt:
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
