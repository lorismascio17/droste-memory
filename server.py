"""MCP server entry point for Droste-Memory."""

from __future__ import annotations

from typing import Any

from core.droste_engine import DrosteConceptEngine

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover - depends on local installation
    raise SystemExit(
        "Missing dependency 'mcp'. Install dependencies with: pip install -r requirements.txt"
    ) from exc


mcp = FastMCP("Droste-Memory")
engine = DrosteConceptEngine()


@mcp.tool()
def inject_concept(
    title: str,
    summary: str,
    detail_content: str,
    zoom_threshold: float = 20.0,
    x: float | None = None,
    y: float | None = None,
) -> dict[str, Any]:
    """Insert a concept into the semantic Droste canvas."""

    return engine.inject_concept(
        title=title,
        summary=summary,
        detail_content=detail_content,
        zoom_threshold=zoom_threshold,
        x=x,
        y=y,
    )


@mcp.tool()
def move_camera_and_zoom(x: float, y: float, zoom_level: float) -> dict[str, Any]:
    """Move the global camera and return currently visible concepts."""

    return engine.move_camera_and_zoom(x=x, y=y, zoom_level=zoom_level)


@mcp.tool()
def get_space_status() -> dict[str, Any]:
    """Return the full memory-space status and macro-level map."""

    return engine.get_space_status()


if __name__ == "__main__":
    mcp.run()
