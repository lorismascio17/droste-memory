"""Packaged MCP server entry point for Droste-Memory."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.droste_engine import DEFAULT_DB_PATH, DrosteConceptEngine
from core.droste_ingester import (
    DEFAULT_CONTEXT_BUDGET,
    DEFAULT_MAX_FILES,
    DEFAULT_MAX_SYMBOLS,
    get_context,
    index_project,
)


def _shard_stats(db_path: Path) -> dict[str, Any]:
    shard_dir = db_path.parent / ".droste" / "nodes"
    files = [path for path in shard_dir.glob("*.json") if path.is_file()] if shard_dir.exists() else []
    return {
        "path": str(shard_dir),
        "count": len(files),
        "bytes": sum(path.stat().st_size for path in files),
    }


def _node_in_root(engine: DrosteConceptEngine, node: Any, root: str | Path | None) -> bool:
    scope = engine.normalize_root(root)
    if scope is None:
        return True
    return engine.normalize_root(node.index_root) == scope


def droste_status_payload(
    engine: DrosteConceptEngine,
    root: str | Path | None = None,
) -> dict[str, Any]:
    migration = engine.ensure_sharded_storage()
    scope_root, root_warning = engine.resolve_query_root(root)
    nodes = [
        node for node in engine.all_nodes()
        if not (root_warning and scope_root is None)
        and _node_in_root(engine, node, scope_root)
    ]
    node_ids = {node.id for node in nodes}
    links = [
        link for link in engine.all_links()
        if link.from_node in node_ids and link.to_node in node_ids
    ]
    shard_stats = _shard_stats(engine.db_path)
    return {
        "storage": "Sharded" if migration.get("storage") == "sharded" else migration.get("storage"),
        "database": str(engine.db_path),
        "root": scope_root,
        "active_root": engine.active_root(),
        "indexed_roots": engine.indexed_roots(),
        "warnings": [root_warning] if root_warning else [],
        "node_count": len(nodes),
        "symbol_count": sum(1 for node in nodes if node.node_type == "symbol"),
        "link_count": len(links),
        "syntax_link_count": sum(1 for link in links if link.type == "syntax_dependency"),
        "shard_dir": shard_stats["path"],
        "shard_count": shard_stats["count"],
        "shard_bytes": shard_stats["bytes"],
        "migration": migration,
    }


def create_mcp_server(db_path: str | Path | None = None) -> Any:
    """Create the FastMCP server.

    The mcp import stays inside this function so normal CLI commands do not
    require the server runtime until `droste mcp` is actually launched.
    """

    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - depends on installation
        raise SystemExit(
            "Missing dependency 'mcp'. Install Droste with: pip install droste-memory"
        ) from exc

    engine = DrosteConceptEngine(db_path=Path(db_path) if db_path else DEFAULT_DB_PATH)
    mcp = FastMCP("Droste-Memory")

    @mcp.tool()
    def droste_index_project(
        path: str,
        reset: bool = False,
        max_files: int = DEFAULT_MAX_FILES,
        max_symbols: int = DEFAULT_MAX_SYMBOLS,
    ) -> dict[str, Any]:
        """Index a project into Droste's local causal graph."""

        result = index_project(
            path,
            engine=engine,
            reset=reset,
            max_files=max_files,
            max_symbols=max_symbols,
        )
        result["active_root"] = engine.active_root()
        return result

    @mcp.tool()
    def droste_get_context(
        query: str = "project",
        budget: int = DEFAULT_CONTEXT_BUDGET,
        root: str | None = None,
    ) -> dict[str, Any]:
        """Return a budgeted causal context slice for an AI agent."""

        return get_context(query, engine=engine, budget=budget, root=root)

    @mcp.tool()
    def droste_status(root: str | None = None) -> dict[str, Any]:
        """Return concise local graph and shard health."""

        return droste_status_payload(engine, root=root)

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

    return mcp


def main(db_path: str | Path | None = None) -> None:
    create_mcp_server(db_path).run()


if __name__ == "__main__":
    main()
