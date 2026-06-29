"""Codex MCP adapter for the local Droste-Memory workspace."""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


SCRIPT_PATH = Path(__file__).resolve()
DEFAULT_ROOT = SCRIPT_PATH.parents[3]
ROOT = Path(os.environ.get("DROSTE_MEMORY_ROOT") or DEFAULT_ROOT).resolve()
PROJECT_DIR = Path(
    os.environ.get("CLAUDE_PROJECT_DIR")
    or os.environ.get("DROSTE_PROJECT_DIR")
    or os.getcwd()
).resolve()
DB_PATH = Path(os.environ.get("DROSTE_DB_PATH") or ROOT / "droste_memory_db.json").resolve()
VISUALIZER_URL = os.environ.get("DROSTE_VISUALIZER_URL", "http://127.0.0.1:5000").rstrip("/")
HTTP_TIMEOUT_SECONDS = float(os.environ.get("DROSTE_HTTP_TIMEOUT_SECONDS", "1.5"))
TRACE_PATH = os.environ.get("DROSTE_MCP_TRACE", "").strip()
OUTPUT_FORMAT = "framed"


def _trace(label: str, payload: Any = "") -> None:
    if not TRACE_PATH:
        return
    try:
        rendered = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {label} {rendered[:5000]}\n"
        with open(TRACE_PATH, "a", encoding="utf-8") as handle:
            handle.write(line)
    except OSError:
        return


def _path_exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return False


LOCAL_PACKAGES = ROOT / ".python-packages"
if _path_exists(LOCAL_PACKAGES):
    sys.path.insert(0, str(LOCAL_PACKAGES))
sys.path.insert(0, str(ROOT))

from core.droste_engine import DrosteConceptEngine
from core.droste_ingester import get_context, index_project, zoom_query


def _engine() -> DrosteConceptEngine:
    return DrosteConceptEngine(db_path=DB_PATH)


def _json_request(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    parsed = urlsplit(VISUALIZER_URL)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 80
    base_path = parsed.path.rstrip("/")

    if parsed.scheme != "http" or host not in {"127.0.0.1", "localhost", "::1"}:
        return None, f"visualizer URL must be local http, got {VISUALIZER_URL!r}"

    body = b"" if payload is None else json.dumps(payload).encode("utf-8")
    target = f"{base_path}{path}" if base_path else path
    request = "\r\n".join(
        [
            f"{method} {target} HTTP/1.1",
            f"Host: {host}:{port}",
            "Accept: application/json",
            "Content-Type: application/json",
            f"Content-Length: {len(body)}",
            "Connection: close",
            "",
            "",
        ]
    ).encode("ascii") + body

    try:
        with socket.create_connection((host, port), timeout=HTTP_TIMEOUT_SECONDS) as sock:
            sock.settimeout(HTTP_TIMEOUT_SECONDS)
            sock.sendall(request)
            chunks: list[bytes] = []
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
    except OSError as exc:
        return None, f"visualizer unavailable: {exc}"

    raw_response = b"".join(chunks)
    header_bytes, _, response_body_bytes = raw_response.partition(b"\r\n\r\n")
    header_text = header_bytes.decode("iso-8859-1", errors="replace")
    status_line = header_text.splitlines()[0] if header_text else ""
    status_parts = status_line.split(" ", 2)
    status_code = int(status_parts[1]) if len(status_parts) > 1 and status_parts[1].isdigit() else 0
    headers = {
        name.strip().lower(): value.strip()
        for line in header_text.splitlines()[1:]
        if ":" in line
        for name, value in [line.split(":", 1)]
    }

    if headers.get("transfer-encoding", "").lower() == "chunked":
        response_body_bytes = _decode_chunked(response_body_bytes)

    response_body = response_body_bytes.decode("utf-8", errors="replace")
    if status_code >= 400:
        return None, f"visualizer HTTP {status_code}: {response_body[:500]}"

    if not response_body:
        return {}, None

    try:
        parsed = json.loads(response_body)
    except json.JSONDecodeError as exc:
        return None, f"visualizer returned invalid JSON: {exc}"

    if not isinstance(parsed, dict):
        return None, "visualizer returned a non-object JSON payload"
    return parsed, None


def _decode_chunked(body: bytes) -> bytes:
    decoded = bytearray()
    remaining = body
    while remaining:
        size_text, _, rest = remaining.partition(b"\r\n")
        if not size_text:
            break
        try:
            size = int(size_text.split(b";", 1)[0], 16)
        except ValueError:
            return body
        if size == 0:
            break
        decoded.extend(rest[:size])
        remaining = rest[size + 2 :]
    return bytes(decoded)


def _with_sync(
    payload: dict[str, Any],
    mode: str,
    warning: str | None = None,
) -> dict[str, Any]:
    result = dict(payload)
    sync: dict[str, Any] = {
        "mode": mode,
        "workspace": str(ROOT),
        "project": str(PROJECT_DIR),
        "database": str(DB_PATH),
        "visualizer_url": VISUALIZER_URL,
    }
    if warning:
        sync["warning"] = warning
    result["_droste_sync"] = sync
    return result


def _status_payload() -> tuple[dict[str, Any], str, str | None]:
    visualizer, warning = _json_request("GET", "/api/status")
    if visualizer is not None:
        return visualizer, "visualizer-api", None
    return _engine().get_space_status(), "local-core", warning


def _state_payload() -> tuple[dict[str, Any], str, str | None]:
    visualizer, warning = _json_request("GET", "/api/state")
    if visualizer is not None:
        return visualizer, "visualizer-api", None
    return _engine().get_visualizer_state(), "local-core", warning


def _move_camera(x: float, y: float, zoom_level: float) -> dict[str, Any]:
    payload = {"x": x, "y": y, "zoom_level": zoom_level}
    visualizer, warning = _json_request("POST", "/api/camera", payload)
    if visualizer is not None:
        return _with_sync(visualizer, "visualizer-api")
    local = _engine().move_camera_and_zoom(x=x, y=y, zoom_level=zoom_level)
    return _with_sync(local, "local-core", warning)


def _node_text(node: dict[str, Any]) -> str:
    return " ".join(
        str(node.get(key, ""))
        for key in ("id", "title", "summary", "detail_content", "detail_preview", "source_path", "node_type")
    ).lower()


def _resolve_project_path(path: str) -> str:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = PROJECT_DIR / candidate
    return str(candidate.resolve())


def _find_node(
    nodes: list[dict[str, Any]],
    node_id: str = "",
    title_query: str = "",
) -> dict[str, Any] | None:
    clean_id = node_id.strip().lower()
    clean_query = title_query.strip().lower()

    if clean_id:
        for node in nodes:
            if str(node.get("id", "")).lower() == clean_id:
                return node
        for node in nodes:
            if clean_id in str(node.get("id", "")).lower():
                return node

    if not clean_query:
        return None

    query_terms = [term for term in clean_query.split() if term]
    best_node: dict[str, Any] | None = None
    best_score = 0
    for node in nodes:
        haystack = _node_text(node)
        score = sum(1 for term in query_terms if term in haystack)
        title = str(node.get("title", "")).lower()
        if clean_query in title:
            score += 3
        if score > best_score:
            best_score = score
            best_node = node

    return best_node if best_score > 0 else None


def _compact_node(node: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {
        "id": node.get("id"),
        "title": node.get("title"),
        "type": node.get("node_type"),
        "zoom_threshold": node.get("zoom_threshold"),
        "x": node.get("x"),
        "y": node.get("y"),
    }
    for key in ("source_path", "line_start", "line_end", "detail_state", "distance", "focus"):
        value = node.get(key)
        if value not in (None, "", []):
            compact[key] = value
    children = node.get("children")
    if isinstance(children, list) and children:
        compact["child_count"] = len(children)
    return compact


def _compact_status(status: dict[str, Any], visible_limit: int = 12, map_limit: int = 16) -> dict[str, Any]:
    visible_nodes = status.get("visible_nodes")
    if not isinstance(visible_nodes, list):
        visible_nodes = []

    all_nodes = status.get("macro_nodes") or status.get("nodes") or []
    if not isinstance(all_nodes, list):
        all_nodes = []

    map_nodes = [
        node
        for node in all_nodes
        if isinstance(node, dict) and node.get("node_type") in {"project", "directory", "file"}
    ]

    return {
        "node_count": status.get("node_count", len(all_nodes)),
        "link_count": status.get("link_count", len(status.get("links") or [])),
        "camera": status.get("camera"),
        "fov": status.get("fov"),
        "visible_count": len(visible_nodes),
        "visible_nodes": [_compact_node(node) for node in visible_nodes[:visible_limit] if isinstance(node, dict)],
        "map_sample_count": min(len(map_nodes), map_limit),
        "map_sample": [_compact_node(node) for node in map_nodes[:map_limit]],
        "updated_at": status.get("updated_at"),
        "database": status.get("database"),
        "note": "Compact MCP status. Use droste_get_context(query, budget) for source snippets or droste_zoom_query(query) to move the camera.",
    }


def droste_remember(
    title: str,
    summary: str,
    detail_content: str,
    zoom_threshold: float = 20.0,
    x: float | None = None,
    y: float | None = None,
) -> dict[str, Any]:
    """Save a concept or document as a coordinated Droste-Memory node."""

    payload = {
        "title": title,
        "summary": summary,
        "detail_content": detail_content,
        "zoom_threshold": zoom_threshold,
    }
    if x is not None or y is not None:
        payload["x"] = x
        payload["y"] = y
    visualizer, warning = _json_request("POST", "/api/inject", payload)
    if visualizer is not None:
        return _with_sync(visualizer, "visualizer-api")

    local = _engine().inject_concept(
        title=title,
        summary=summary,
        detail_content=detail_content,
        zoom_threshold=zoom_threshold,
        x=x,
        y=y,
    )
    return _with_sync(local, "local-core", warning)


def droste_pan_zoom(x: float, y: float, zoom_level: float) -> dict[str, Any]:
    """Move the Droste virtual camera and return the visible memory nodes."""

    return _move_camera(x=x, y=y, zoom_level=zoom_level)


def droste_focus_node(
    node_id: str = "",
    title_query: str = "",
    reveal: bool = True,
    zoom_level: float = 0.0,
) -> dict[str, Any]:
    """Focus the camera on a node by id or text query, optionally revealing details."""

    status = _engine().get_space_status()
    mode = "local-core"
    warning = None
    nodes = status.get("macro_nodes") or status.get("nodes") or []
    if not isinstance(nodes, list):
        nodes = []

    node = _find_node(nodes, node_id=node_id, title_query=title_query)
    if node is None:
        return _with_sync(
            {
                "status": "not_found",
                "message": "No matching Droste node found.",
                "query": {"node_id": node_id, "title_query": title_query},
                "node_count": len(nodes),
            },
            mode,
            warning,
        )

    threshold = float(node.get("zoom_threshold", 20.0) or 20.0)
    if zoom_level > 0:
        target_zoom = zoom_level
    elif reveal:
        target_zoom = threshold * 1.15
    else:
        target_zoom = max(1.0, threshold * 0.75)

    moved = _move_camera(
        x=float(node.get("x", 0.0) or 0.0),
        y=float(node.get("y", 0.0) or 0.0),
        zoom_level=target_zoom,
    )
    moved["focused_node"] = node
    moved["requested_reveal"] = reveal
    return moved


def droste_context_view(
    query: str = "",
    reveal: bool = False,
    zoom_level: float = 0.0,
) -> dict[str, Any]:
    """Return the current canvas, or focus the camera on a query-matched node."""

    if query.strip():
        return droste_focus_node(title_query=query, reveal=reveal, zoom_level=zoom_level)

    return droste_status()


def droste_status() -> dict[str, Any]:
    """Return macro map, camera, field of view, and visible Droste nodes."""

    status = _engine().get_space_status()
    return _with_sync(_compact_status(status), "local-core")


def droste_visualizer_state() -> dict[str, Any]:
    """Return the state payload used by the local FastAPI canvas visualizer."""

    state, mode, warning = _state_payload()
    return _with_sync(state, mode, warning)


def droste_index_project(
    path: str,
    reset: bool = False,
    max_files: int = 600,
    max_symbols: int = 2400,
    max_file_bytes: int = 512000,
) -> dict[str, Any]:
    """Index a project into nested Droste nodes.

    As of 0.4.1+auto-boot the graph is built automatically on server boot
    (see `_auto_boot_index`); this tool is now a MANUAL OVERRIDE, used mainly
    for a forced `reset=True` rebuild or to index an extra project path.
    """

    resolved_path = _resolve_project_path(path)
    payload = {
        "path": resolved_path,
        "reset": reset,
        "max_files": max_files,
        "max_symbols": max_symbols,
        "max_file_bytes": max_file_bytes,
    }
    visualizer, warning = _json_request("POST", "/api/index", payload)
    if visualizer is not None:
        return _with_sync(visualizer, "visualizer-api")

    local = index_project(
        resolved_path,
        engine=_engine(),
        reset=reset,
        max_files=max_files,
        max_symbols=max_symbols,
        max_file_bytes=max_file_bytes,
    )
    return _with_sync(local, "local-core", warning)


def droste_zoom_query(query: str) -> dict[str, Any]:
    """Search Droste-Memory and move the camera to the best matching node."""

    payload = {"query": query}
    visualizer, warning = _json_request("POST", "/api/zoom-query", payload)
    if visualizer is not None:
        return _with_sync(visualizer, "visualizer-api")

    local = zoom_query(query, engine=_engine())
    return _with_sync(local, "local-core", warning)


def droste_get_context(query: str, budget: int = 6000) -> dict[str, Any]:
    """Compile only the useful Droste nodes for a query within a character budget."""

    payload = {"query": query, "budget": budget}
    visualizer, warning = _json_request("POST", "/api/context", payload)
    if visualizer is not None:
        return _with_sync(visualizer, "visualizer-api")

    local = get_context(query, engine=_engine(), budget=budget)
    return _with_sync(local, "local-core", warning)


def _tool_definitions() -> list[dict[str, Any]]:
    return [
        {
            "name": "droste_remember",
            "description": "Save a concept or document as a coordinated Droste-Memory node.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "summary": {"type": "string"},
                    "detail_content": {"type": "string"},
                    "zoom_threshold": {"type": "number", "default": 20.0},
                    "x": {"type": "number", "minimum": -1.0, "maximum": 1.0},
                    "y": {"type": "number", "minimum": -1.0, "maximum": 1.0},
                },
                "required": ["title", "summary", "detail_content"],
            },
        },
        {
            "name": "droste_pan_zoom",
            "description": "Move the Droste virtual camera and return visible memory nodes.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "x": {"type": "number", "minimum": -1.0, "maximum": 1.0},
                    "y": {"type": "number", "minimum": -1.0, "maximum": 1.0},
                    "zoom_level": {"type": "number", "exclusiveMinimum": 0.0},
                },
                "required": ["x", "y", "zoom_level"],
            },
        },
        {
            "name": "droste_focus_node",
            "description": "Focus the camera on a node by id or text query, optionally revealing details.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "node_id": {"type": "string", "default": ""},
                    "title_query": {"type": "string", "default": ""},
                    "reveal": {"type": "boolean", "default": True},
                    "zoom_level": {"type": "number", "default": 0.0},
                },
            },
        },
        {
            "name": "droste_context_view",
            "description": "Return the current canvas, or focus the camera on a query-matched node.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "default": ""},
                    "reveal": {"type": "boolean", "default": False},
                    "zoom_level": {"type": "number", "default": 0.0},
                },
            },
        },
        {
            "name": "droste_status",
            "description": "Return macro map, camera, field of view, and visible Droste nodes.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "droste_visualizer_state",
            "description": "Return the state payload used by the local FastAPI canvas visualizer.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "droste_index_project",
            "description": "Index a project into nested Droste nodes: project, directories, files, and code symbols.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "reset": {"type": "boolean", "default": False},
                    "max_files": {"type": "integer", "default": 600, "minimum": 1},
                    "max_symbols": {"type": "integer", "default": 2400, "minimum": 0},
                    "max_file_bytes": {"type": "integer", "default": 512000, "minimum": 1024},
                },
                "required": ["path"],
            },
        },
        {
            "name": "droste_zoom_query",
            "description": "Search Droste-Memory and move the camera to the matching concept at the needed zoom level.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                },
                "required": ["query"],
            },
        },
        {
            "name": "droste_get_context",
            "description": "Compile only the useful source or memory snippets for a query within a character budget.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "budget": {"type": "integer", "default": 6000, "minimum": 500},
                },
                "required": ["query"],
            },
        },
    ]


def _call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    handlers = {
        "droste_remember": droste_remember,
        "droste_pan_zoom": droste_pan_zoom,
        "droste_focus_node": droste_focus_node,
        "droste_context_view": droste_context_view,
        "droste_status": droste_status,
        "droste_visualizer_state": droste_visualizer_state,
        "droste_index_project": droste_index_project,
        "droste_zoom_query": droste_zoom_query,
        "droste_get_context": droste_get_context,
    }
    if name not in handlers:
        raise KeyError(f"Unknown tool: {name}")
    return handlers[name](**arguments)


def _read_message() -> dict[str, Any] | None:
    global OUTPUT_FORMAT

    first_line = sys.stdin.buffer.readline()
    if not first_line:
        _trace("stdin_closed")
        return None

    if first_line.lstrip().startswith(b"{"):
        OUTPUT_FORMAT = "json-line"
        message = json.loads(first_line.decode("utf-8"))
        _trace("read_json_line", message)
        return message

    OUTPUT_FORMAT = "framed"
    headers: dict[str, str] = {}
    line = first_line
    while line and line not in {b"\r\n", b"\n"}:
        name, _, value = line.decode("ascii", errors="replace").partition(":")
        headers[name.lower()] = value.strip()
        line = sys.stdin.buffer.readline()

    length = int(headers.get("content-length", "0"))
    if length <= 0:
        _trace("read_empty_framed_message", headers)
        return None

    body = sys.stdin.buffer.read(length)
    message = json.loads(body.decode("utf-8"))
    _trace("read_framed", message)
    return message


def _write_message(message: dict[str, Any]) -> None:
    _trace("write", message)
    body = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if OUTPUT_FORMAT == "json-line":
        sys.stdout.buffer.write(body + b"\n")
        sys.stdout.buffer.flush()
        return

    sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii"))
    sys.stdout.buffer.write(body)
    sys.stdout.buffer.flush()


def _success(message_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def _error(message_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": message}}


def _handle_request(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    message_id = message.get("id")
    params = message.get("params") or {}

    if message_id is None:
        return None

    if method == "initialize":
        protocol_version = params.get("protocolVersion", "2024-11-05")
        return _success(
            message_id,
            {
                "protocolVersion": protocol_version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "droste-memory", "version": "0.4.1+auto-boot"},
            },
        )

    if method == "tools/list":
        return _success(message_id, {"tools": _tool_definitions()})

    if method == "tools/call":
        name = str(params.get("name", ""))
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return _error(message_id, -32602, "Tool arguments must be an object.")
        try:
            result = _call_tool(name, arguments)
            text = json.dumps(result, ensure_ascii=False, indent=2)
            return _success(message_id, {"content": [{"type": "text", "text": text}], "isError": False})
        except Exception as exc:  # pragma: no cover - defensive MCP boundary
            return _success(
                message_id,
                {
                    "content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}],
                    "isError": True,
                },
            )

    if method == "ping":
        return _success(message_id, {})

    if method == "resources/list":
        return _success(message_id, {"resources": []})

    if method == "prompts/list":
        return _success(message_id, {"prompts": []})

    return _error(message_id, -32601, f"Method not found: {method}")


# ---- Zero-config auto-boot (0.4.1+auto-boot) --------------------------------
# The graph is auto-indexed on first boot so the user never runs a manual
# command. NOTE: this MCP process is stateless per call (`_engine()` rebuilds
# from disk each time); the persistent in-RAM singleton + ReactiveWatcher live
# in the visualizer. Auto-boot therefore only ensures the project graph EXISTS
# on disk; reactive watching is the visualizer's job (default-on).

AUTO_BOOT_ENABLED = os.environ.get("DROSTE_AUTO_BOOT", "1") == "1"
_INDEX_LOCK_PATH = DB_PATH.with_name(DB_PATH.name + ".indexing.lock")


def _db_has_project(project_dir: Path) -> bool:
    """True if the DB already holds a populated project node for this path."""
    try:
        data = json.loads(DB_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    target = str(project_dir)
    for node in data.get("nodes", []):
        if node.get("node_type") != "project" or not node.get("source_path"):
            continue
        try:
            same = str(Path(node["source_path"]).resolve()) == target
        except OSError:
            same = False
        if same and node.get("children"):
            return True
    return False


def _acquire_index_lock(stale_seconds: float = 180.0) -> bool:
    """Exclusive cross-process lock: only one indexer writes the shared DB.

    This is the guard against the last-writer-wins races that corrupted the
    JSON DB when an MCP reindex, a visualizer /api/index, and the watcher all
    wrote concurrently. Stale locks (crashed indexer) are reclaimed by age.
    """
    try:
        if _INDEX_LOCK_PATH.exists():
            try:
                if time.time() - _INDEX_LOCK_PATH.stat().st_mtime > stale_seconds:
                    _INDEX_LOCK_PATH.unlink()
            except OSError:
                pass
        handle = os.open(str(_INDEX_LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(handle, str(os.getpid()).encode("ascii"))
        os.close(handle)
        return True
    except (FileExistsError, OSError):
        return False


def _release_index_lock() -> None:
    try:
        _INDEX_LOCK_PATH.unlink()
    except OSError:
        pass


def _auto_boot_index() -> None:
    """Index the detected project once, off the request loop, if absent.

    Never resets (only appends a missing project), routes through the stateful
    visualizer when up, and serializes on an exclusive lock so it cannot race
    another writer.
    """
    project = PROJECT_DIR
    if not project.is_dir():
        _trace("auto_boot_skip", {"reason": "not_a_directory", "project": str(project)})
        return
    if _db_has_project(project):
        _trace("auto_boot_skip", {"reason": "graph_present", "project": str(project)})
        return
    if not _acquire_index_lock():
        _trace("auto_boot_skip", {"reason": "indexer_active", "project": str(project)})
        return
    try:
        payload = {
            "path": str(project),
            "reset": False,
            "max_files": 600,
            "max_symbols": 2400,
            "max_file_bytes": 512000,
        }
        visualizer, warning = _json_request("POST", "/api/index", payload)
        if visualizer is not None:
            _trace("auto_boot_done", {"mode": "visualizer-api", "project": str(project)})
        else:
            index_project(str(project), engine=_engine(), reset=False)
            _trace("auto_boot_done", {"mode": "local-core", "warning": warning, "project": str(project)})
    except Exception as exc:  # never crash the server on a boot-index failure
        _trace("auto_boot_error", repr(exc))
    finally:
        _release_index_lock()


def _start_auto_boot() -> None:
    if not AUTO_BOOT_ENABLED:
        _trace("auto_boot_disabled", {"project": str(PROJECT_DIR)})
        return
    threading.Thread(target=_auto_boot_index, name="droste-auto-boot", daemon=True).start()
    _trace("auto_boot_thread_started", {"project": str(PROJECT_DIR)})


def run_stdio() -> None:
    _trace("server_start", {"root": str(ROOT), "project": str(PROJECT_DIR), "db": str(DB_PATH)})
    _start_auto_boot()  # non-blocking: must run before the read loop, never block initialize
    while True:
        message = _read_message()
        if message is None:
            break
        response = _handle_request(message)
        if response is not None:
            _write_message(response)
    _trace("server_stop")


if __name__ == "__main__":
    run_stdio()
