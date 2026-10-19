"""Root-scoped index health, symbol lookup and explainable references."""
from __future__ import annotations

from collections import deque
import hashlib
from pathlib import Path
from typing import Any

from .droste_engine import DrosteConceptEngine, DrosteNode


def check_index(engine: DrosteConceptEngine, root=None, verify_content: bool = True) -> dict[str, Any]:
    from .droste_ingester import DrosteProjectIngester, INDEX_VERSION
    scope, warning = engine.resolve_query_root(root)
    result: dict[str, Any] = {"root": scope, "status": "unverified", "warnings": [],
                              "verification": "sha256" if verify_content else "size_mtime"}
    if warning:
        result["warnings"].append(warning)
    state = engine.index_state(scope) if scope else None
    if not state:
        result["warnings"].append("No verifiable index snapshot. Run droste index on the project first.")
        return result
    config = state["config"]
    project = Path(scope)
    try:
        candidates = DrosteProjectIngester(engine)._collect_files(project, config["max_files"] + 1)
    except OSError as exc:
        result.update(status="stale", scan_error=str(exc))
        result["warnings"].append("Cannot scan the indexed project. Check its path and permissions before using cached snippets.")
        return result
    current = {p.relative_to(project).as_posix(): p for p in candidates[:config["max_files"]]}
    previous = state["files"]
    changed, unreadable = [], []
    for rel in sorted(current.keys() & previous.keys()):
        try:
            path = current[rel]
            if verify_content:
                differs = hashlib.sha256(path.read_bytes()).hexdigest() != previous[rel]["sha256"]
            else:
                stat = path.stat()
                differs = stat.st_size != previous[rel]["size"] or stat.st_mtime_ns != previous[rel]["mtime_ns"]
            if differs:
                changed.append(rel)
        except OSError:
            unreadable.append(rel)
    added, deleted = sorted(current.keys() - previous.keys()), sorted(previous.keys() - current.keys())
    partial = list(state.get("partial_reasons", []))
    if len(candidates) > config["max_files"] and "file_limit" not in partial:
        partial.append("file_limit")
    outdated = state.get("index_version") != INDEX_VERSION
    stale = bool(added or deleted or changed or unreadable or not project.is_dir() or outdated)
    result.update(status="stale" if stale else ("partial" if partial else "fresh"),
                  indexed_at=state["indexed_at"], partial=bool(partial), partial_reasons=partial,
                  embedding_model=state.get("embedding_model"),
                  resolution=state.get("resolution", {}),
                  counts={"added": len(added), "changed": len(changed), "deleted": len(deleted), "unreadable": len(unreadable)},
                  added=added[:50], changed=changed[:50], deleted=deleted[:50], unreadable=unreadable[:50])
    if stale:
        result["warnings"].append("Index is stale. Re-index the project or run droste watch before trusting its references or snippets.")
    if outdated:
        result["warnings"].append("Index was built by an older extractor; re-index to rebuild symbol resolution.")
    if partial:
        result["warnings"].append("Index coverage is partial: " + ", ".join(partial) + ". Increase the relevant index limits.")
    return result


def _scoped_nodes(engine, root):
    scope, warning = engine.resolve_query_root(root)
    nodes = [] if warning and scope is None else [n for n in engine.all_nodes()
        if n.node_type == "symbol" and (scope is None or engine.normalize_root(n.index_root) == scope)]
    return scope, nodes


def _name(node: DrosteNode) -> str:
    return node.title.split(": ", 1)[-1]


def _symbol(node: DrosteNode, nodes: list[DrosteNode], root: str | None) -> dict[str, Any]:
    enclosing = sorted([n for n in nodes if n.source_path == node.source_path
        and n.line_start is not None and node.line_start is not None
        and n.line_start < node.line_start <= (n.line_end or n.line_start)], key=lambda n: n.line_start)
    path = node.source_path or ""
    if root and path:
        try:
            path = Path(path).relative_to(root).as_posix()
        except ValueError:
            pass
    return {"id": node.id, "name": _name(node), "qualified_name": ".".join([*map(_name, enclosing), _name(node)]),
            "path": path, "line_start": node.line_start, "line_end": node.line_end,
            "signature": next((line.strip() for line in node.detail_content.splitlines() if line.strip()), "")[:300]}


def _file_matches(node: DrosteNode, file: str | None, root: str | None) -> bool:
    if not file:
        return True
    path = Path(file)
    path = path if path.is_absolute() else Path(root or ".") / path
    return Path(node.source_path or "").resolve() == path.resolve()


def _public_symbols(nodes, root):
    by_file: dict[str | None, list] = {}
    for node in nodes:
        by_file.setdefault(node.source_path, []).append(node)
    return {n.id: _symbol(n, by_file[n.source_path], root) for n in nodes}


def find_symbols(engine: DrosteConceptEngine, query: str, root=None, file: str | None = None, limit: int = 20):
    scope, nodes = _scoped_nodes(engine, root)
    health = check_index(engine, scope, verify_content=False)
    query = query.strip().lower()
    if not query:
        raise ValueError("A symbol query is required")
    public = _public_symbols(nodes, scope)
    matches = [public[n.id] for n in nodes if _file_matches(n, file, scope)]
    matches = [m for m in matches if query in m["qualified_name"].lower() or query == m["id"].lower()]
    matches.sort(key=lambda m: (m["name"].lower() != query, m["qualified_name"].lower() != query, m["path"], m["line_start"] or 0))
    return {"status": "ok", "root": scope, "total": len(matches), "matches": matches[:max(1, min(200, limit))],
            "index_health": health, "warnings": health["warnings"]}


def references(engine: DrosteConceptEngine, target: str, root=None, file: str | None = None,
               direction: str = "callers", depth: int = 1, limit: int = 100):
    if direction not in {"callers", "callees", "both"}:
        raise ValueError("direction must be callers, callees or both")
    if not 1 <= depth <= 5 or not 1 <= limit <= 500:
        raise ValueError("depth must be 1..5 and limit 1..500")
    scope, nodes = _scoped_nodes(engine, root)
    health = check_index(engine, scope, verify_content=False)
    public = _public_symbols(nodes, scope)
    matches = [n for n in nodes if _file_matches(n, file, scope) and
               target in {n.id, _name(n), public[n.id]["qualified_name"]}]
    base = {"root": scope, "warnings": health["warnings"], "index_health": health,
            "scope": "Static indexed references; dynamic calls and unresolved receivers may be absent."}
    if len(matches) != 1:
        return {**base, "status": "ambiguous" if matches else "not_found",
                "candidates": [public[n.id] for n in matches[:50]], "candidate_count": len(matches), "references": []}
    origin = matches[0]
    outgoing: dict[str, list] = {}
    incoming: dict[str, list] = {}
    for link in engine.all_links():
        if link.from_node in public and link.to_node in public:
            outgoing.setdefault(link.from_node, []).append(link)
            incoming.setdefault(link.to_node, []).append(link)
    queue = deque([(origin.id, 0)])
    visited, edges, seen_edges = {origin.id}, [], set()
    truncated = False
    while queue:
        node_id, distance = queue.popleft()
        if distance >= depth:
            continue
        adjacent = []
        if direction in {"callers", "both"}:
            adjacent.extend((l, l.from_node, "caller") for l in incoming.get(node_id, []))
        if direction in {"callees", "both"}:
            adjacent.extend((l, l.to_node, "callee") for l in outgoing.get(node_id, []))
        for link, next_id, role in sorted(adjacent, key=lambda item: (item[0].from_node, item[0].to_node)):
            key = (link.from_node, link.to_node, link.type, link.label)
            if key in seen_edges:
                continue
            seen_edges.add(key)
            if len(edges) == limit:
                truncated = True
                continue
            edges.append({"depth": distance + 1, "role": role, "from": public[link.from_node],
                          "to": public[link.to_node], "type": link.type, "label": link.label,
                          "resolution": link.resolution or "legacy_unverified", "evidence": link.evidence[:20],
                          "evidence_count": len(link.evidence)})
            if next_id not in visited:
                visited.add(next_id)
                queue.append((next_id, distance + 1))
    return {**base, "status": "ok", "target": public[origin.id], "direction": direction,
            "depth": depth, "references": edges, "truncated": truncated}
