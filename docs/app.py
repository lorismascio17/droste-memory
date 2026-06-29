"""Local FastAPI dashboard for the Droste-Memory JSON database."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from core.droste_engine import DrosteConceptEngine
from core.droste_ingester import (
    DrosteProjectIngester,
    get_context,
    index_project,
    zoom_query,
)


VISUALIZER_DIR = Path(__file__).resolve().parent
TEMPLATE_PATH = VISUALIZER_DIR / "templates" / "index.html"
COCKPIT_PATH = VISUALIZER_DIR / "cockpit.html"

app = FastAPI(title="Droste-Memory Visualizer", version="0.4.0+reactive")
engine = DrosteConceptEngine()
_reactive_watcher = None


@app.on_event("startup")
def _start_reactive_watcher() -> None:
    """Elastic-space hot-reload, ON by default (0.4.1+auto-boot zero-config).

    Shares the singleton `engine` so get_context sees new files without a
    restart, watching every indexed project root. Disable with DROSTE_REACTIVE=0.
    """
    global _reactive_watcher
    if os.environ.get("DROSTE_REACTIVE", "1") == "0":
        print("[reactive] watcher disabled (DROSTE_REACTIVE=0)")
        return
    from core.droste_watcher import ReactiveWatcher

    interval = float(os.environ.get("DROSTE_REACTIVE_INTERVAL", "1.0"))
    _reactive_watcher = ReactiveWatcher(
        DrosteProjectIngester(engine), poll_interval=interval
    ).start()
    print(f"[reactive] elastic-space watcher live (poll={interval}s)")


@app.on_event("shutdown")
def _stop_reactive_watcher() -> None:
    if _reactive_watcher is not None:
        _reactive_watcher.stop()


@app.get("/api/reactive")
def api_reactive() -> dict[str, Any]:
    return {
        "enabled": _reactive_watcher is not None,
        "roots": _reactive_watcher.discover_roots() if _reactive_watcher else [],
        "last_event": _reactive_watcher.last_event if _reactive_watcher else None,
    }


class InjectPayload(BaseModel):
    title: str = Field(..., min_length=1)
    summary: str = ""
    detail_content: str = ""
    zoom_threshold: float = Field(default=20.0, gt=0)
    x: float | None = Field(default=None, ge=-1.0, le=1.0)
    y: float | None = Field(default=None, ge=-1.0, le=1.0)


class CameraPayload(BaseModel):
    x: float
    y: float
    zoom_level: float = Field(default=1.0, gt=0)


class IndexPayload(BaseModel):
    path: str
    reset: bool = False
    max_files: int = Field(default=600, ge=1, le=5000)
    max_symbols: int = Field(default=2400, ge=0, le=20000)
    max_file_bytes: int = Field(default=512_000, ge=1024, le=5_000_000)


class QueryPayload(BaseModel):
    query: str = Field(..., min_length=1)
    budget: int = Field(default=6000, ge=500, le=200000)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(COCKPIT_PATH, media_type="text/html")


@app.get("/cockpit.html")
def cockpit() -> FileResponse:
    return FileResponse(COCKPIT_PATH, media_type="text/html")


@app.get("/graph.json")
def graph_json() -> FileResponse:
    return FileResponse(VISUALIZER_DIR / "graph.json", media_type="application/json")


@app.get("/status.json")
def status_json() -> FileResponse:
    return FileResponse(VISUALIZER_DIR / "status.json", media_type="application/json")


@app.get("/context.json")
def context_json() -> FileResponse:
    return FileResponse(VISUALIZER_DIR / "context.json", media_type="application/json")


@app.get("/legacy", response_class=HTMLResponse)
def legacy() -> HTMLResponse:
    return HTMLResponse(TEMPLATE_PATH.read_text(encoding="utf-8"))


@app.get("/api/state")
def api_state() -> dict[str, Any]:
    return engine.get_visualizer_state()


@app.get("/api/status")
def api_status() -> dict[str, Any]:
    return engine.get_space_status()


@app.post("/api/inject")
def api_inject(payload: InjectPayload) -> dict[str, Any]:
    try:
        return engine.inject_concept(
            title=payload.title,
            summary=payload.summary,
            detail_content=payload.detail_content,
            zoom_threshold=payload.zoom_threshold,
            x=payload.x,
            y=payload.y,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/camera")
def api_camera(payload: CameraPayload) -> dict[str, Any]:
    return engine.move_camera_and_zoom(
        x=payload.x,
        y=payload.y,
        zoom_level=payload.zoom_level,
    )


@app.post("/api/reset")
def api_reset() -> dict[str, Any]:
    return engine.reset_space()


@app.post("/api/index")
def api_index(payload: IndexPayload) -> dict[str, Any]:
    try:
        return index_project(
            payload.path,
            engine=engine,
            reset=payload.reset,
            max_files=payload.max_files,
            max_symbols=payload.max_symbols,
            max_file_bytes=payload.max_file_bytes,
        )
    except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/zoom-query")
def api_zoom_query(payload: QueryPayload) -> dict[str, Any]:
    return zoom_query(payload.query, engine=engine)


@app.post("/api/context")
def api_context(payload: QueryPayload) -> dict[str, Any]:
    return get_context(payload.query, engine=engine, budget=payload.budget)


@app.post("/api/demo/droste")
def api_droste_demo() -> dict[str, Any]:
    engine.reset_space()
    macro = engine.inject_concept(
        title="Sviluppo Mobile con Flutter e Supabase",
        summary="Architettura generale dell'applicazione",
        detail_content="Panoramica della struttura cross-platform dell'app.",
        zoom_threshold=1.0,
        x=0.0,
        y=0.0,
    )
    micro = engine.inject_concept(
        title="Flutter JWT Token Refresh",
        summary="Codice logico per il refresh automatico della sessione",
        detail_content=(
            "class TokenRefresh {\n"
            "  final SupabaseClient client;\n"
            "  final Duration skew;\n"
            "  bool _refreshing = false;\n"
            "  Future<Session?> refreshIfNeeded() async {\n"
            "    final session = client.auth.currentSession;\n"
            "    if (session == null) return null;\n"
            "    final expiresAt = DateTime.fromMillisecondsSinceEpoch(session.expiresAt! * 1000);\n"
            "    if (expiresAt.difference(DateTime.now()) > skew) return session;\n"
            "    _refreshing = true;\n"
            "    try { return (await client.auth.refreshSession()).session; }\n"
            "    finally { _refreshing = false; }\n"
            "  }\n"
            "}"
        ),
        zoom_threshold=15.0,
        x=0.0,
        y=0.0,
    )
    camera = engine.move_camera_and_zoom(x=0.0, y=0.0, zoom_level=1.0)
    return {
        "status": "seeded",
        "macro": macro["node"],
        "micro": micro["node"],
        "camera": camera["camera"],
        "state": engine.get_visualizer_state(),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("visualizer.app:app", host="127.0.0.1", port=5000, reload=False)
