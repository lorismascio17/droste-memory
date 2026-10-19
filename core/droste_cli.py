"""Command line interface for Droste-Memory."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib import error, request


def _configure_windows_utf8_output(
    platform: str = sys.platform,
    stdout: Any = sys.stdout,
    stderr: Any = sys.stderr,
) -> None:
    if platform.startswith("win"):
        if hasattr(stdout, "reconfigure"):
            try:
                stdout.reconfigure(encoding="utf-8", errors="replace")
                stderr.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


_configure_windows_utf8_output()


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.droste_engine import DEFAULT_DB_PATH, DrosteConceptEngine
from core.droste_ingester import DrosteProjectIngester, droste_zoom_query


VERSION = "v1.2.1-Alpha-Sharded"
VISUALIZER_CAMERA_URL = "http://127.0.0.1:5000/api/camera"

RESET = "\033[0m"
GREEN = "\033[92m"
CYAN = "\033[96m"
WHITE = "\033[97m"
DIM = "\033[2m"


RADIAL_ART = r"""
                  .-----------------------.
             .----'           |           '----.
         .---'          .-----+-----.          '---.
       .'          .----'     |     '----.          '.
      /        .---'      .---+---.      '---.        \
     /      .-'        .-'    |    '-.        '-.      \
    |     .'        .-'   .---+---.   '-.        '.     |
    |    /        .'    .'    |    '.    '.        \    |
    |   |        |     |   .--+--.   |     |        |   |
    | --+--------+-----+---+  @  +---+-----+--------+-- |
    |   |        |     |   '--+--'   |     |        |   |
    |    \        '.    '.    |    .'    .'        /    |
    |     '.        '-.   '---+---'   .-'        .'     |
     \      '-.        '-.    |    .-'        .-'      /
      \        '---.      '---+---'      .---'        /
       '.          '----.     |     .----'          .'
         '---.          '-----+-----'          .---'
             '----.           |           .----'
                  '-----------------------'
"""


def _wants_color(enabled: bool | None = None) -> bool:
    if enabled is not None:
        return enabled
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


def _paint(text: str, color: str, enabled: bool) -> str:
    return f"{color}{text}{RESET}" if enabled else text


def print_splash(color: bool | None = None) -> None:
    enabled = _wants_color(color)
    art_lines = RADIAL_ART.strip("\n").splitlines()
    for index, line in enumerate(art_lines):
        print(_paint(line, CYAN if index % 2 == 0 else GREEN, enabled))
    print()
    print(_paint("DROSTE-MEMORY // RIGID FRACTAL RADIAL LAYOUT", WHITE, enabled))
    print(_paint(f"Local Graph Engine {VERSION}", WHITE, enabled))
    print()
    print(_paint("Commands", CYAN, enabled))
    print("  droste index <path> [--reset]")
    print("  droste status")
    print("  droste check [--json]")
    print("  droste find <query> [--file path]")
    print("  droste references <symbol> [--direction callers|callees|both]")
    print("  droste watch <path>")
    print("  droste zoom <symbol_name>")
    print("  droste context [query] --budget 1500")
    print("  droste mcp")
    print()
    print(_paint("Fast path: droste context hub_core --budget 1000 | clip", DIM, enabled))


def print_compact_header(color: bool | None = None) -> None:
    enabled = _wants_color(color)
    print(_paint(f"DROSTE-MEMORY {VERSION}", WHITE, enabled))
    print(_paint("Rigid Fractal Radial Layout", CYAN, enabled))


def _human_bytes(size: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.2f} {unit}"
        value /= 1024
    return f"{size} B"


def _load_meta(db_path: Path) -> dict[str, Any]:
    if not db_path.exists():
        return {"storage": "missing", "error": None}
    try:
        return json.loads(db_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"storage": "corrupt", "error": str(exc)}
    except OSError as exc:
        return {"storage": "unreadable", "error": str(exc)}


def _storage_label(db_path: Path) -> str:
    meta = _load_meta(db_path)
    storage = meta.get("storage")
    if storage == "sharded":
        return "Sharded"
    if storage in {"missing", "corrupt", "unreadable"}:
        return str(storage).title()
    if isinstance(meta.get("nodes"), list) and meta.get("nodes"):
        return "Legacy inline (migrates on next graph save)"
    return "Inline empty"


def _shard_stats(shard_dir: Path) -> dict[str, Any]:
    if not shard_dir.exists():
        return {"path": str(shard_dir), "count": 0, "bytes": 0}
    files = [path for path in shard_dir.glob("*.json") if path.is_file()]
    return {
        "path": str(shard_dir),
        "count": len(files),
        "bytes": sum(path.stat().st_size for path in files),
    }


def _engine(db_path: Path | None = None) -> DrosteConceptEngine:
    return DrosteConceptEngine(db_path=db_path or DEFAULT_DB_PATH)


def command_status(args: argparse.Namespace) -> int:
    engine = _engine(args.db)
    from core.droste_mcp import droste_status_payload
    payload = droste_status_payload(engine, root=getattr(args, "root", None))
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    print_compact_header()
    print("status")
    print(f"  migration:     {payload['migration']['status']}")
    print(f"  storage:       {payload['storage']}")
    print(f"  database:      {payload['database']}")
    print(f"  live nodes:    {payload['node_count']}")
    print(f"  symbols:       {payload['symbol_count']}")
    print(f"  links:         {payload['link_count']}")
    print(f"  syntax links:  {payload['syntax_link_count']}")
    print(f"  shard dir:     {payload['shard_dir']}")
    print(f"  shard files:   {payload['shard_count']}")
    print(f"  shard cache:   {_human_bytes(int(payload['shard_bytes']))}")
    print(f"  index health:  {payload['index_health']['status']}")
    for warning in payload["warnings"]:
        print("WARNING: " + warning, file=sys.stderr)
    return 0


def _post_visualizer_camera(camera: dict[str, Any], url: str) -> tuple[bool, str]:
    payload = {
        "x": camera.get("x", 0.0),
        "y": camera.get("y", 0.0),
        "zoom_level": camera.get("zoom", 1.0),
    }
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=1.5) as response:
            return True, f"HTTP {response.status}"
    except (OSError, error.URLError, error.HTTPError) as exc:
        return False, str(exc)


def _open_editor(source_path: str | None, line_start: int | None) -> tuple[bool, str]:
    if not source_path:
        return False, "no source_path on focused node"
    line = max(1, int(line_start or 1))
    target = f"{source_path}:{line}"
    code = shutil.which("code")
    if code:
        subprocess.Popen([code, "--goto", target])
        return True, f"code --goto {target}"
    return False, target


def command_zoom(args: argparse.Namespace) -> int:
    engine = _engine(args.db)
    result = droste_zoom_query(args.symbol_name, engine=engine)
    if result.get("status") != "focused":
        print(f"not found: {args.symbol_name}", file=sys.stderr)
        return 2

    node = result["focused_node"]
    camera = result["camera"]
    print(f"focused: {node['title']}")
    print(f"  node:   {node['id']}")
    print(f"  type:   {node['node_type']}")
    print(f"  source: {node.get('source_path') or '(none)'}")
    print(f"  line:   {node.get('line_start') or '(none)'}")
    print(f"  camera: x={camera['x']:.4f} y={camera['y']:.4f} zoom={camera['zoom']:.2f}")

    if not args.no_visualizer:
        ok, message = _post_visualizer_camera(camera, args.visualizer_url)
        status = "sent" if ok else "not sent"
        print(f"  visualizer: {status} ({message})")

    if not args.no_open:
        opened, message = _open_editor(node.get("source_path"), node.get("line_start"))
        status = "opened" if opened else "fallback"
        print(f"  editor: {status} ({message})")
    return 0


def command_context(args: argparse.Namespace) -> int:
    engine = _engine(args.db)
    ingester = DrosteProjectIngester(engine)
    result = ingester.get_context(args.query, budget=args.budget, root=args.root)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    for warning in result.get("warnings", []):
        print("WARNING: " + warning, file=sys.stderr)
    print(result.get("compiled_context", ""))
    return 0


def command_inspect(args: argparse.Namespace) -> int:
    from core.inspection import check_index, find_symbols, references
    engine = _engine(args.db)
    if args.command == "check":
        result = check_index(engine, args.root)
    elif args.command == "find":
        result = find_symbols(engine, args.query, root=args.root, file=args.file, limit=args.limit)
    else:
        result = references(engine, args.target, root=args.root, file=args.file,
                            direction=args.direction, depth=args.depth, limit=args.limit)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        for warning in result.get("warnings", []):
            print("WARNING: " + warning, file=sys.stderr)
        print(f"{args.command}: {result['status']}")
        if args.command == "check":
            print(f"  root: {result['root']}")
            print(f"  verification: {result['verification']}")
            for category in ("added", "changed", "deleted", "unreadable"):
                for path in result.get(category, []):
                    print(f"  {category}: {path}")
        elif args.command == "find" or result["status"] != "ok":
            for node in result.get("matches", result.get("candidates", [])):
                print(f"  {node['qualified_name']}  {node['path']}:{node['line_start']}  [{node['id']}]")
        else:
            for ref in result["references"]:
                source, target = ref["from"], ref["to"]
                print(f"  {source['qualified_name']} ({source['path']}:{source['line_start']}) -> "
                      f"{target['qualified_name']} ({target['path']}:{target['line_start']}) [{ref['resolution']}]")
            if result["truncated"]:
                print("  Results truncated; increase --limit or reduce --depth.")
    if args.command == "check":
        return 0 if result["status"] == "fresh" else 1
    return 0 if result["status"] == "ok" else 2


def command_watch(args: argparse.Namespace) -> int:
    from core.droste_watcher import ReactiveWatcher
    ingester = DrosteProjectIngester(_engine(args.db))
    ingester.index_project(args.path, max_files=args.max_files, max_symbols=args.max_symbols,
        max_symbols_per_lang=args.max_symbols_per_lang, max_file_bytes=args.max_file_bytes)
    watcher = ReactiveWatcher(ingester, roots=[args.path], poll_interval=args.interval)
    watcher.poll_once()
    print(f"Watching {Path(args.path).resolve()} (poll={watcher.poll_interval}s). Ctrl+C to stop.", flush=True)
    try:
        while True:
            time.sleep(watcher.poll_interval)
            for event in watcher.poll_once():
                print(json.dumps(event, ensure_ascii=False), flush=True)
    except KeyboardInterrupt:
        return 0


def command_index(args: argparse.Namespace) -> int:
    from core import treesitter_extract

    path = Path(args.path).expanduser()
    if not path.is_dir():
        print(f"not a directory: {path}", file=sys.stderr)
        return 2

    engine = _engine(args.db)
    ingester = DrosteProjectIngester(engine)
    result = ingester.index_project(
        str(path),
        reset=args.reset,
        max_files=args.max_files,
        max_symbols=args.max_symbols,
        max_symbols_per_lang=args.max_symbols_per_lang,
        max_file_bytes=args.max_file_bytes,
    )
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    stats = result["stats"]
    ts_on = result.get("treesitter_available", treesitter_extract.available())
    print_compact_header()
    print(f"indexed {path}")
    print(f"  files:         {stats['file_count']}")
    print(f"  symbols:       {stats['symbol_count']}")
    print(f"  links:         {stats['link_count']}")
    print(f"  syntax edges:  {result.get('syntax_dependency_links', 0)}")
    print(f"  reused files:  {result.get('reused_files', 0)}")
    print(f"  embeddings:    {engine.projector.backend}")
    if result.get("partial"):
        print("WARNING: Partial index: " + ", ".join(result["partial_reasons"]), file=sys.stderr)
    if result.get("embedding_warning"):
        print("WARNING: " + result["embedding_warning"], file=sys.stderr)
    if ts_on:
        print("  tree-sitter:   on (polyglot call-graph active)")
    else:
        print("  tree-sitter:   OFF — non-Python files degrade to symbols "
              "without causal edges")
        print("                 fix: pip install tree-sitter-language-pack",
              file=sys.stderr)
    return 0


def command_view(args: argparse.Namespace) -> int:
    """Serve a snapshot of the selected database without modifying the package."""
    import http.server
    import tempfile
    import webbrowser
    from functools import partial
    from visualizer import export_graph

    engine = _engine(args.db)
    nodes = engine.all_nodes()
    roots = sorted({n.index_root for n in nodes if n.index_root})
    if not roots:
        print("no indexed roots ? run `droste index <path>` first", file=sys.stderr)
        return 2
    requested = args.root
    if requested is None:
        cwd = engine.normalize_root(Path.cwd())
        requested = next((r for r in roots if engine.normalize_root(r) == cwd), None)
        requested = requested or engine.active_root()
    try:
        chosen = export_graph._pick_root(engine, requested)
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 2

    assets = Path(export_graph.__file__).resolve().parent
    # Only public assets and this project's snapshot are served. Temporary
    # storage works with read-only installations and isolates concurrent views.
    with tempfile.TemporaryDirectory(prefix="droste-view-") as directory:
        view_dir = Path(directory)
        shutil.copyfile(assets / "cockpit.html", view_dir / "cockpit.html")
        counts = export_graph.export(chosen, view_dir / "graph.json", engine=engine)
        (view_dir / "status.json").write_text(json.dumps({
            "node_count": sum(counts[k] for k in ("project", "directory", "file", "symbol")),
            "symbol_count": counts["symbol"],
            "link_count": counts["edge"],
            "syntax_link_count": counts["edge"],
            "counts": counts,
        }), encoding="utf-8")
        handler = partial(http.server.SimpleHTTPRequestHandler, directory=directory)
        with http.server.ThreadingHTTPServer(("127.0.0.1", args.port), handler) as httpd:
            url = f"http://127.0.0.1:{httpd.server_port}/cockpit.html?web=1"
            print_compact_header()
            print(f"view  ->  {url}")
            print(f"  project: {counts['symbol']} symbols / {counts['edge']} causal edges "
                  f"across {counts['file']} files")
            print("  Snapshot of the index; rerun after re-indexing. Ctrl+C to stop", flush=True)
            if not args.no_open:
                webbrowser.open(url)
            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                print("\nstopped")
    return 0


def command_mcp(args: argparse.Namespace) -> int:
    from core.droste_mcp import main as run_mcp

    run_mcp(args.db)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="droste",
        description="Droste-Memory cyberpunk graph CLI.",
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="Database path (default: DROSTE_DB or user data directory).")
    parser.add_argument("--version", action="version", version=f"droste {VERSION}")
    sub = parser.add_subparsers(dest="command")

    index = sub.add_parser("index", help="Index a project into the local graph.")
    index.add_argument("path", help="Project root directory to index.")
    index.add_argument("--reset", action="store_true",
                       help="Wipe this root's prior nodes before indexing.")
    index.add_argument("--max-files", type=int, default=2000)
    index.add_argument("--max-symbols", type=int, default=20000)
    index.add_argument("--max-symbols-per-lang", type=int, default=3000)
    index.add_argument("--max-file-bytes", type=int, default=512000)
    index.add_argument("--json", action="store_true", help="Emit the full index result JSON.")
    index.set_defaults(func=command_index)

    status = sub.add_parser("status", help="Show local graph health.")
    status.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    status.add_argument("--root", default=None)
    status.set_defaults(func=command_status)

    zoom = sub.add_parser("zoom", help="Focus a symbol in editor and visualizer.")
    zoom.add_argument("symbol_name")
    zoom.add_argument("--no-open", action="store_true", help="Do not launch the editor.")
    zoom.add_argument(
        "--no-visualizer",
        action="store_true",
        help="Do not POST camera coordinates to the local visualizer.",
    )
    zoom.add_argument("--visualizer-url", default=VISUALIZER_CAMERA_URL)
    zoom.set_defaults(func=command_zoom)

    context = sub.add_parser("context", help="Emit compressed LLM context.")
    context.add_argument("query", nargs="?", default="project")
    context.add_argument("--budget", type=int, default=1500, help="Character budget (minimum 500), including separators.")
    context.add_argument("--root", default=None, help="Limit retrieval to one indexed root.")
    context.add_argument("--json", action="store_true", help="Emit full context payload JSON.")
    context.set_defaults(func=command_context)

    for name, help_text in [("check", "Verify index hashes, freshness and coverage."),
                            ("find", "Find symbols with index IDs and source lines."),
                            ("references", "Inspect callers/callees with source evidence.")]:
        command = sub.add_parser(name, help=help_text)
        command.add_argument("--root", default=None)
        command.add_argument("--json", action="store_true")
        if name != "check":
            command.add_argument("query" if name == "find" else "target")
            command.add_argument("--file", default=None, help="Exact project-relative or absolute source path.")
            command.add_argument("--limit", type=int, default=20 if name == "find" else 100)
        if name == "references":
            command.add_argument("--direction", choices=["callers", "callees", "both"], default="callers")
            command.add_argument("--depth", type=int, default=1)
        command.set_defaults(func=command_inspect)

    watch = sub.add_parser("watch", help="Index and refresh supported files, including deletions.")
    watch.add_argument("path")
    watch.add_argument("--interval", type=float, default=1.0)
    watch.add_argument("--max-files", type=int, default=2000)
    watch.add_argument("--max-symbols", type=int, default=20000)
    watch.add_argument("--max-symbols-per-lang", type=int, default=3000)
    watch.add_argument("--max-file-bytes", type=int, default=512000)
    watch.set_defaults(func=command_watch)

    view = sub.add_parser("view", help="Open the fractal visualizer on the live graph.")
    view.add_argument("--root", default=None, help="Indexed root to view (default: most recent).")
    view.add_argument("--port", type=int, default=7878)
    view.add_argument("--no-open", action="store_true", help="Serve without opening a browser.")
    view.set_defaults(func=command_view)

    mcp = sub.add_parser("mcp", help="Run the stdio MCP server.")
    mcp.set_defaults(func=command_mcp)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        print_splash()
        return 0
    try:
        return int(args.func(args))
    except (ValueError, FileNotFoundError, NotADirectoryError, PermissionError) as exc:
        print(f"droste: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
