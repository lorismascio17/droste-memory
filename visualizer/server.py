"""Dependency-free HTTP server for the Universe Code Viewer."""

from __future__ import annotations

import argparse
import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


VISUALIZER_DIR = Path(__file__).resolve().parent
CAMERA: dict[str, Any] = {"x": 0.0, "y": 0.0, "zoom_level": 1.0}


class UniverseHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(VISUALIZER_DIR), **kwargs)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self.path = "/cockpit.html"
        elif path == "/api/camera":
            self._send_json({"camera": CAMERA})
            return
        super().do_GET()

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/camera":
            self.send_error(404, "Unknown endpoint")
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(content_length) or b"{}")
            CAMERA.update({
                "x": float(payload["x"]),
                "y": float(payload["y"]),
                "zoom_level": max(0.01, float(payload.get("zoom_level", 1.0))),
            })
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            self._send_json({"error": str(exc)}, status=400)
            return
        self._send_json({"status": "focused", "camera": CAMERA})

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve the Droste Universe Code Viewer.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), UniverseHandler)
    print(f"Droste Universe Code Viewer: http://{args.host}:{args.port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
