"""Packaged MCP server entry point for Droste-Memory."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.droste_engine import DEFAULT_DB_PATH, DrosteConceptEngine
from core.droste_ingester import (
    DEFAULT_CONTEXT_BUDGET,
    DEFAULT_MAX_FILES,
    DEFAULT_MAX_SYMBOLS,
    DEFAULT_MAX_SYMBOLS_PER_LANG,
    DEFAULT_MAX_FILE_BYTES,
    get_context,
    index_project,
)


def _shard_stats(shard_dir: Path) -> dict[str, Any]:
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
    shard_stats = _shard_stats(engine._shard_dir())
    from .inspection import check_index
    health = check_index(engine, root=scope_root, verify_content=False)
    return {
        "storage": "Sharded" if migration.get("storage") == "sharded" else migration.get("storage"),
        "database": str(engine.db_path),
        "root": scope_root,
        "active_root": engine.active_root(),
        "indexed_roots": engine.indexed_roots(),
        "warnings": health["warnings"],
        "index_health": health,
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
        max_symbols_per_lang: int = DEFAULT_MAX_SYMBOLS_PER_LANG,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    ) -> dict[str, Any]:
        """Index a project into Droste's local causal graph."""

        result = index_project(
            path,
            engine=engine,
            reset=reset,
            max_files=max_files,
            max_symbols=max_symbols,
            max_symbols_per_lang=max_symbols_per_lang,
            max_file_bytes=max_file_bytes,
        )
        result["active_root"] = engine.active_root()
        return result

    @mcp.tool()
    def droste_get_context(
        query: str = "project",
        budget: int = DEFAULT_CONTEXT_BUDGET,
        root: str | None = None,
    ) -> dict[str, Any]:
        """Return source context within a character budget (minimum 500), with static references and index-health warnings."""

        return get_context(query, engine=engine, budget=budget, root=root)

    @mcp.tool(structured_output=False)
    def droste_explore(query: str = "project", tokens: int = 2000, level: str = "map",
                       focus: str | None = None, root: str | None = None,
                       encoding: str = "o200k_base", seen: list[str] | None = None) -> str:
        """Search, then zoom map -> structure -> source using returned node IDs as focus. Returns ONE compact JSON text block, <= tokens (256..64000) including metadata under the chosen encoding; protocol/model wrappers are excluded. Per-item level may be lower if source cannot fit. Pass seen handles ONLY for content you still retain; clear them after compaction. Source hashes are verified; stale indexes require re-indexing. Static links can miss dynamic calls."""
        from .exploration import explore
        return explore(engine, query, tokens=tokens, level=level, focus=focus, root=root,
                       encoding=encoding, seen=seen)

    @mcp.tool()
    def droste_status(root: str | None = None) -> dict[str, Any]:
        """Return concise local graph and shard health."""

        return droste_status_payload(engine, root=root)

    @mcp.tool()
    def droste_check_index(root: str | None = None) -> dict[str, Any]:
        """Verify source SHA-256 hashes, added/deleted files and index coverage before using code context."""
        from .inspection import check_index
        return check_index(engine, root=root)

    @mcp.tool()
    def droste_find_symbols(query: str, root: str | None = None, file: str | None = None, limit: int = 20) -> dict[str, Any]:
        """Find symbol IDs, qualified names and source lines. Use file to disambiguate common names."""
        from .inspection import find_symbols
        return find_symbols(engine, query, root=root, file=file, limit=limit)

    @mcp.tool()
    def droste_references(target: str, root: str | None = None, file: str | None = None,
                          direction: str = "callers", depth: int = 1, limit: int = 100) -> dict[str, Any]:
        """Find static callers/callees with resolution provenance. Ambiguous names return candidates; repeat with a symbol ID or file. Depth 1..5, limit 1..500."""
        from .inspection import references
        return references(engine, target, root=root, file=file, direction=direction, depth=depth, limit=limit)

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
