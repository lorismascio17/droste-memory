"""Project ingester and query utilities for Droste-Memory.

The ingester turns a codebase into a spatial hierarchy:

project -> directories -> files -> symbols/sections

Source files remain the source of truth. Droste nodes store orientation,
line references, concise summaries, and selected snippets so an agent can use
the memory as a camera and compile only the context it needs.
"""

from __future__ import annotations

import ast
import hashlib
import json
import math
import re
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .droste_engine import DrosteConceptEngine, DrosteNode, clamp, utc_now
from . import treesitter_extract


INDEX_VERSION = 3  # 3: SQL/RPC graph edges + critical-node smart zoom
DEFAULT_MAX_FILES = 600
# Global symbol ceiling. Raised 2400->8000 once minified/bundle files are
# filtered out (they used to burn the whole budget). Paired with a per-language
# cap so one language can't cannibalise the others (Python fell 164->35 edges on
# a mixed repo when a JS bundle ate the global budget before Python was reached).
DEFAULT_MAX_SYMBOLS = 8000
DEFAULT_MAX_SYMBOLS_PER_LANG = 3000
# Non-code families (docs/markup) carry no call-graph — only section nodes — so
# they get a much tighter per-language budget than real code, keeping markdown
# from flooding the embedding pass (a doc-heavy repo produced 3000 .md sections).
# Keyed by _language_of() output (suffix for non-code types).
LANG_SYMBOL_CAP = {
    ".md": 600, ".mdx": 600, ".txt": 150,
    ".html": 400, ".css": 200, ".scss": 200,
    ".json": 100, ".yaml": 100, ".yml": 100, ".toml": 100,
    "sql": 1000,
}
DEFAULT_MAX_FILE_BYTES = 512_000
DEFAULT_CONTEXT_BUDGET = 6000
# v0.4.2+packer-fix: strict budget enforcement constants.
# A focus node may overflow the cap ONLY if its full form is this small — a
# genuine micro-answer that must always be shown whole. Anything larger is
# decomposed (full -> contract -> file-skeleton) to fit the budget.
MICRO_FOCUS_CHARS = 1500
# External callers are appended as compact, distinct contract stubs, never
# allowed to drag in a heavy file body.
CALLER_STUB_CHARS = 250
# Below this remaining headroom there is no point scanning further candidates.
MIN_USEFUL_SECTION = 120
# v0.6.0+hybrid: a node with no lexical token overlap is admitted as a seed only
# when its embedding cosine to the query clears this floor (concept-intent recall
# without flooding the candidate pool with weak semantic noise). Tuned for the
# fastembed bge-small distribution (unrelated pairs ~0.48, true synonyms ~0.70);
# the deterministic hash fallback rarely clears it, which is correct — hash has
# no real synonym signal to contribute.
SEMANTIC_MATCH_MIN = 0.55

# v0.4.2: language families for syntax_dependency edges. A reference resolves
# only to a definition in the SAME family, so a Python call to `sorted` never
# links to a JS function of the same name (kills cross-language false wormholes
# like live-server.mjs `resolve` showing up as a Python callee).
_LANG_BY_EXT = {
    ".py": "python", ".pyi": "python",
    ".js": "jsts", ".mjs": "jsts", ".cjs": "jsts", ".jsx": "jsts",
    ".ts": "jsts", ".tsx": "jsts",
    ".go": "go", ".rs": "rust", ".dart": "dart", ".java": "java",
    ".sql": "sql",
    ".rb": "ruby", ".php": "php",
    ".swift": "swift", ".kt": "kotlin", ".kts": "kotlin",
    ".c": "c", ".h": "c",
    ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp", ".c++": "cpp",
    ".hpp": "cpp", ".hh": "cpp", ".hxx": "cpp",
}


def _language_of(path: str | None) -> str:
    """Coarse language family for a source path. Unknown extensions fall back to
    the raw suffix, so two identical unknown types still match and two different
    ones still don't."""
    if not path:
        return ""
    suffix = Path(path).suffix.lower()
    return _LANG_BY_EXT.get(suffix, suffix)

SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".python-packages",
    ".plugin-python-packages",
    ".plugin-mcp-runtime",
    "node_modules",
    ".next",
    "dist",
    "build",
    "coverage",
    ".dart_tool",
    "out",
    "outputs",
    "generated",
    "gen",
    "Pods",
}

# Fix (b): deterministic guard against generated/minified bundles. A single
# minified file can hold thousands of tokens that flood the symbol budget and
# the call-graph with noise (a JS bundle produced 1281 phantom edges on a real
# repo). Skipped by name suffix or by a "huge average line length" content sniff.
MINIFIED_SUFFIXES = (
    ".min.js", ".min.mjs", ".min.cjs", ".min.css", ".min.ts",
    ".bundle.js", "_bundle.js", "-bundle.js", ".bundle.css",
    "-min.js", ".pack.js",
)
# Only sniff content for web-asset extensions prone to minification.
_MINIFY_SNIFF_EXTS = {".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".css", ".scss"}
_MINIFY_AVG_LINE_LEN = 500

SKIP_FILES = {
    "droste_memory_db.json",
    "droste_memory_db.json.tmp",
}

TEXT_EXTENSIONS = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".mjs",
    ".cjs",
    ".html",
    ".css",
    ".scss",
    ".md",
    ".mdx",
    ".json",
    ".toml",
    ".yaml",
    ".yml",
    ".txt",
    ".ps1",
    ".sh",
    ".go",
    ".rs",
    ".dart",
    ".sql",
    ".swift",
    ".kt",
    ".kts",
    ".c",
    ".h",
    ".cc",
    ".cpp",
    ".cxx",
    ".c++",
    ".hpp",
    ".hh",
    ".hxx",
}

SYMBOL_PATTERN = re.compile(
    r"^\s*(?:export\s+)?(?:async\s+)?(?:function|class)\s+([A-Za-z_$][\w$]*)"
    r"|^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\(?[^=]*\)?\s*=>",
)

# v0.5.0: declarative multi-language definition registry. Each spec is a
# (definition regex, name group, kind, block-end strategy). Brace languages
# reuse the existing _find_block_end balance counter; the rest fall back to a
# fixed window. Wormholes built from these remain heuristic ("dependency"), but
# the same-language gate in _build_dependency_links keeps them isolated.
LANG_DEF_SPECS: dict[str, list[dict[str, Any]]] = {
    "go": [
        {"re": re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)\s*\("), "kind": "function", "block": "brace"},
        {"re": re.compile(r"^\s*type\s+([A-Za-z_]\w*)\s+(?:struct|interface)\b"), "kind": "class", "block": "brace"},
    ],
    "rust": [
        {"re": re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?(?:unsafe\s+)?fn\s+([A-Za-z_]\w*)"), "kind": "function", "block": "brace"},
        {"re": re.compile(r"^\s*(?:pub\s+)?(?:struct|enum|trait|impl|mod)\s+([A-Za-z_]\w*)"), "kind": "class", "block": "brace"},
    ],
    "dart": [  # Flutter
        {"re": re.compile(r"^\s*(?:abstract\s+)?(?:class|mixin|extension|enum)\s+([A-Za-z_]\w*)"), "kind": "class", "block": "brace"},
        {"re": re.compile(r"^\s*(?:[A-Za-z_][\w<>,\s\?]*\s+)?([A-Za-z_]\w*)\s*\([^;{]*\)\s*(?:async\s*)?\{"), "kind": "function", "block": "brace"},
    ],
}

# Maps file extension to a LANG_DEF_SPECS key (the spec families that have no
# native AST and go through the generic brace-based extractor / contracts).
EXT_TO_SPEC_LANG = {
    ".go": "go", ".rs": "rust", ".dart": "dart",
}

HEADING_PATTERN = re.compile(r"^(#{1,4})\s+(.+?)\s*$")
CALL_REFERENCE_PATTERN = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")
EXTENDS_REFERENCE_PATTERN = re.compile(r"\bextends\s+([A-Za-z_$][\w$]*)")
PYTHON_BASE_PATTERN = re.compile(r"^\s*class\s+\w+\(([^)]*)\)\s*:", re.MULTILINE)
SQL_FUNCTION_PATTERN = re.compile(
    r"\bCREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+"
    r"((?:(?:\"[^\"]+\"|[A-Za-z_][\w$]*)\.)?(?:\"[^\"]+\"|[A-Za-z_][\w$]*))\s*\(",
    re.IGNORECASE,
)
SQL_DOLLAR_QUOTE_PATTERN = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$")
RPC_REFERENCE_PATTERN = re.compile(
    r"\.rpc\s*(?:<[^>]+>)?\s*\(\s*(?:r|R|f|F)?['\"]([A-Za-z_][\w$]*)['\"]"
)
EDGE_FUNCTION_REFERENCE_PATTERNS = (
    re.compile(
        r"\.functions\s*\.\s*invoke\s*(?:<[^>]+>)?\s*\(\s*"
        r"(?:r|R|f|F)?['\"]([A-Za-z0-9_-]+)['\"]"
    ),
    re.compile(r"/functions/v1/([A-Za-z0-9_-]+)"),
)
# Cross-language DB bridge: every database object becomes a first-class link
# TARGET so app code in ANY language can wormhole into it. Beyond FUNCTION we
# also capture PROCEDURE/TRIGGER (callables) and TABLE/VIEW/TYPE (data the app
# references by name via PostgREST `.from('t')` / raw SQL).
SQL_OBJECT_PATTERN = re.compile(
    r"\bCREATE\s+(?:OR\s+REPLACE\s+)?"
    r"(?:GLOBAL\s+|LOCAL\s+|TEMP\s+|TEMPORARY\s+|UNLOGGED\s+|MATERIALIZED\s+|RECURSIVE\s+)*"
    r"(FUNCTION|PROCEDURE|TABLE|VIEW|TRIGGER|TYPE)\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"((?:\"[^\"]+\"|[A-Za-z_][\w$]*)(?:\.(?:\"[^\"]+\"|[A-Za-z_][\w$]*))?)",
    re.IGNORECASE,
)
SQL_OBJECT_KIND = {
    "FUNCTION": "function", "PROCEDURE": "function", "TRIGGER": "function",
    "TABLE": "class", "VIEW": "class", "TYPE": "class",
}
# App-side references to DB tables: Supabase/knex `.from('t')` / `.into('t')`,
# plus raw-SQL `FROM/JOIN/INTO/UPDATE <table>` embedded in code strings (ORMs,
# raw queries) in any language.
DB_TABLE_REFERENCE_PATTERNS = (
    re.compile(r"\.(?:from|into)\s*\(\s*(?:r|R|f|F)?['\"]([A-Za-z_][\w$]*)['\"]"),
    re.compile(r"\b(?:FROM|JOIN|INTO|UPDATE)\s+(?:ONLY\s+)?(?:\"([A-Za-z_][\w$]*)\"|([a-z_][\w$]*))", re.IGNORECASE),
)
# Generic cross-language bridge: a string literal that exactly names a symbol
# defined in ANOTHER language becomes a wormhole (handler names, RPC names,
# template ids, channel names …). Guarded by length + a stopword list so common
# words don't manufacture phantom edges.
STRING_LITERAL_PATTERN = re.compile(r"['\"]([A-Za-z_][A-Za-z0-9_]{3,})['\"]")
CROSS_LANG_STOPWORDS = {
    "data", "name", "type", "user", "value", "error", "true", "false", "null", "none",
    "list", "item", "items", "count", "index", "text", "json", "string", "number",
    "object", "array", "status", "result", "results", "message", "label", "title",
    "input", "output", "field", "fields", "table", "query", "params", "param", "body",
    "headers", "default", "content", "email", "public", "private", "select", "insert",
    "update", "delete", "from", "where", "order", "group", "limit", "offset", "child",
    "children", "color", "width", "height", "style", "class", "props", "state", "event",
}
MAX_CROSS_LANG_PER_SYMBOL = 12
CRITICAL_LOD_KEYWORDS = (
    "jwt",
    "secret",
    "stripe",
    "paywall",
    "crypto",
    "verify",
    "rpc",
)

REFERENCE_STOPWORDS = {
    "await",
    "bool",
    "dict",
    "except",
    "false",
    "float",
    "for",
    "if",
    "int",
    "len",
    "list",
    "max",
    "min",
    "none",
    "print",
    "range",
    "return",
    "self",
    "str",
    "super",
    "true",
}

# v0.5.0: dynamic-zoom intent vectors. A "broad" query widens the zoom (many
# nodes, low fidelity → contracts); a "detail" query tightens it (few nodes,
# full fidelity). Bilingual (it/en) to match how this graph is queried.
_BROAD_HINTS = (
    "overview", "panoramica", "architettura", "architecture", "come funziona",
    "how does", "mappa", "map", "tutti", "struttura", "structure", "flow", "flusso",
)
_DETAIL_HINTS = (
    "implementazione", "implementation", "body", "corpo", "riga", "line",
    "esattamente", "exact", "bug", "perche", "perché", "why", "definizione completa",
)
_TEST_DOC_QUERY_HINTS = {
    "test", "tests", "spec", "readme", "doc", "docs",
    "pytest", "vitest", "jest",
}
_RUNTIME_PATH_MARKERS = ("/src/", "/lib/", "/app/", "/api/", "/core/")
_TEST_DOC_PATH_MARKERS = ("/test/", "/tests/", "/spec/", "/docs/", "/doc/")


@dataclass(frozen=True)
class IndexStats:
    root: str
    node_count: int
    directory_count: int
    file_count: int
    symbol_count: int
    link_count: int
    skipped_files: int
    truncated_files: int
    deduped_files: int = 0


class DrosteProjectIngester:
    """Build and query a nested Droste index for a project."""

    def __init__(self, engine: DrosteConceptEngine | None = None) -> None:
        self.engine = engine or DrosteConceptEngine()
        # In-process memo of the 14MB embed cache, guarded by the file mtime so a
        # cross-process write is still picked up. A warm re-index only needs to
        # resolve a handful of keys for the changed files, yet re-read+parsed the
        # whole cache from disk every time (~0.38s) — pure waste once it is in RAM.
        self._embed_cache_mem: dict[str, list[float]] | None = None
        self._embed_cache_mtime: float = -1.0

    def index_project(
        self,
        path: str | Path,
        *,
        reset: bool = False,
        max_files: int = DEFAULT_MAX_FILES,
        max_symbols: int = DEFAULT_MAX_SYMBOLS,
        max_symbols_per_lang: int = DEFAULT_MAX_SYMBOLS_PER_LANG,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    ) -> dict[str, Any]:
        root = Path(path).expanduser().resolve()
        if not root.exists():
            raise FileNotFoundError(f"Project path does not exist: {root}")
        if not root.is_dir():
            raise NotADirectoryError(f"Project path must be a directory: {root}")

        files = self._collect_files(root, max_files=max_files)
        now = utc_now()
        nodes: dict[str, DrosteNode] = {}
        directory_file_counts: dict[str, int] = {}
        skipped_files = 0
        truncated_files = 0
        deduped_files = 0
        seen_file_hashes: dict[str, str] = {}
        symbol_count = 0
        symbol_count_by_lang: dict[str, int] = {}
        symbol_records: list[dict[str, Any]] = []
        definitions_by_name: dict[str, list[DrosteNode]] = {}

        # v0.6.3+file-skip: persistent per-file registry (rel_path -> sha1 +
        # the node ids it produced) lets an unchanged file be transplanted whole
        # from the live graph instead of re-parsed. registry_all spans every
        # index_root; we only ever touch this root's slice. existing_by_id snaps
        # the previous index's node objects (still in the engine until the
        # replace below) so reuse can pull them by id with embeddings intact.
        registry_all = self._load_file_registry()
        prev_registry = {} if reset else registry_all.get(str(root), {})
        existing_by_id: dict[str, DrosteNode] = (
            {} if reset else {node.id: node for node in self.engine.all_nodes()}
        )
        new_registry: dict[str, Any] = {}
        reused_file_count = 0

        root_id = self._stable_id("project", root.name, str(root))
        root_node = DrosteNode(
            id=root_id,
            title=root.name or str(root),
            summary=f"Project root indexed by Droste-Memory: {root}",
            detail_content=f"Index root: {root}\nFiles considered: {len(files)}",
            node_type="project",
            source_path=str(root),
            index_root=str(root),
            x=0.0,
            y=0.0,
            semantic_x=0.0,
            semantic_y=0.0,
            fixed_x=0.0,
            fixed_y=0.0,
            zoom_threshold=0.45,
            created_at=now,
            updated_at=now,
        )
        nodes[root_id] = root_node

        def ensure_directory(directory: Path) -> DrosteNode:
            rel = self._relative_path(directory, root)
            if rel == ".":
                return root_node
            node_id = self._stable_id("directory", rel, str(root))
            existing = nodes.get(node_id)
            if existing:
                return existing
            parent = ensure_directory(directory.parent)
            depth = len(Path(rel).parts)
            node = DrosteNode(
                id=node_id,
                title=f"dir: {rel}",
                summary=f"Directory {rel}",
                detail_content=f"Directory: {rel}\nParent: {parent.title}",
                node_type="directory",
                parent_id=parent.id,
                source_path=str(directory),
                index_root=str(root),
                zoom_threshold=2.0 + depth * 1.5,
                created_at=now,
                updated_at=now,
            )
            nodes[node_id] = node
            parent.children.append(node.id)
            return node

        for file_path in files:
            if len([node for node in nodes.values() if node.node_type == "file"]) >= max_files:
                skipped_files += 1
                continue

            try:
                raw = file_path.read_bytes()
            except OSError:
                skipped_files += 1
                continue

            if b"\x00" in raw:
                skipped_files += 1
                continue
            if len(raw) > max_file_bytes:
                raw = raw[:max_file_bytes]
                truncated_files += 1

            content_hash = hashlib.sha1(raw).hexdigest()
            if content_hash in seen_file_hashes:
                # Byte-identical to an already-indexed file (e.g. skill docs
                # copied under both .agents/ and .claude/). Index once; skip the
                # duplicate so it does not pollute search/context with twins.
                deduped_files += 1
                continue
            seen_file_hashes[content_hash] = self._relative_path(file_path, root)

            rel = self._relative_path(file_path, root)
            parent = ensure_directory(file_path.parent)

            # v0.6.3+file-skip: FILE-LEVEL CONTENT-HASH SKIP. If this file's
            # SHA-1 matches the last valid index AND every node it produced is
            # still in the live graph, transplant its file+symbol nodes (with
            # their embeddings and references) straight from the previous index
            # — no decode, no tree-sitter parse, no embedding. Cross-file links
            # are still rebuilt globally below (cheap), so wormholes to/from any
            # file that DID change stay correct. Disabled on a forced reset.
            reg_entry = prev_registry.get(rel)
            if (
                not reset
                and reg_entry is not None
                and reg_entry.get("sha1") == content_hash
                and existing_by_id.get(reg_entry.get("file_node_id")) is not None
                and all(
                    existing_by_id.get(sym.get("node_id")) is not None
                    for sym in reg_entry.get("symbols", [])
                )
            ):
                file_node = existing_by_id[reg_entry["file_node_id"]]
                file_node.parent_id = parent.id
                file_node.children = []
                file_node.index_root = str(root)
                file_node.updated_at = now
                nodes[file_node.id] = file_node
                parent.children.append(file_node.id)
                directory_file_counts[parent.id] = directory_file_counts.get(parent.id, 0) + 1

                reused_symbols = reg_entry.get("symbols", [])
                for sym_rec in reused_symbols:
                    sym = existing_by_id[sym_rec["node_id"]]
                    sym.parent_id = file_node.id
                    sym.children = []
                    sym.index_root = str(root)
                    sym.updated_at = now
                    nodes[sym.id] = sym
                    file_node.children.append(sym.id)
                    definitions_by_name.setdefault(str(sym_rec.get("name")), []).append(sym)
                    symbol_records.append({
                        "node": sym,
                        "snippet": sym.detail_content,
                        "name": str(sym_rec.get("name")),
                        "kind": str(sym_rec.get("kind")),
                        "rel": rel,
                        "references": sym_rec.get("references"),
                        "ast_based": bool(sym_rec.get("ast_based")),
                        "ts_based": bool(sym_rec.get("ts_based")),
                    })
                lang = _language_of(str(file_path))
                symbol_count += len(reused_symbols)
                symbol_count_by_lang[lang] = symbol_count_by_lang.get(lang, 0) + len(reused_symbols)
                new_registry[rel] = {
                    "sha1": content_hash,
                    "file_node_id": file_node.id,
                    "symbols": reused_symbols,
                }
                reused_file_count += 1
                continue

            text = raw.decode("utf-8", errors="replace")
            line_count = text.count("\n") + (1 if text else 0)
            symbols = self._extract_symbols(file_path, text)
            # Fix (c): per-language budget on top of the global ceiling, so one
            # language (e.g. a Dart lib or a JS bundle) can't exhaust the budget
            # before another language's files are reached.
            lang = _language_of(str(file_path))
            lang_used = symbol_count_by_lang.get(lang, 0)
            lang_cap = LANG_SYMBOL_CAP.get(lang, max_symbols_per_lang)
            remaining_symbol_budget = max(0, min(
                max_symbols - symbol_count,
                lang_cap - lang_used,
            ))
            symbols = symbols[:remaining_symbol_budget]
            symbol_count += len(symbols)
            symbol_count_by_lang[lang] = lang_used + len(symbols)

            file_id = self._stable_id("file", rel, content_hash)
            file_node = DrosteNode(
                id=file_id,
                title=f"file: {rel}",
                summary=f"{file_path.suffix or 'text'} file, {line_count} lines, {len(symbols)} indexed symbols",
                detail_content=self._file_detail(rel, text, symbols, truncated=len(raw) >= max_file_bytes),
                node_type="file",
                parent_id=parent.id,
                source_path=str(file_path),
                line_start=1,
                line_end=line_count,
                index_root=str(root),
                content_hash=content_hash,
                zoom_threshold=7.0 + self._path_depth(rel) * 1.5,
                created_at=now,
                updated_at=now,
            )
            nodes[file_id] = file_node
            parent.children.append(file_node.id)
            directory_file_counts[parent.id] = directory_file_counts.get(parent.id, 0) + 1

            registry_symbols: list[dict[str, Any]] = []
            for symbol in symbols:
                symbol_id = self._stable_id(
                    "symbol",
                    rel,
                    f"{symbol['kind']}:{symbol['name']}:{symbol['line_start']}",
                )
                snippet = self._slice_lines(text, symbol["line_start"], symbol["line_end"])
                symbol_node = DrosteNode(
                    id=symbol_id,
                    title=f"{symbol['kind']}: {symbol['name']}",
                    summary=f"{symbol['kind']} in {rel}:{symbol['line_start']}-{symbol['line_end']}",
                    detail_content=snippet,
                    node_type="symbol",
                    parent_id=file_node.id,
                    source_path=str(file_path),
                    line_start=symbol["line_start"],
                    line_end=symbol["line_end"],
                    index_root=str(root),
                    content_hash=hashlib.sha1(snippet.encode("utf-8", errors="replace")).hexdigest(),
                    zoom_threshold=14.0 + self._path_depth(rel) * 1.5,
                    created_at=now,
                    updated_at=now,
                )
                nodes[symbol_id] = symbol_node
                file_node.children.append(symbol_node.id)
                definitions_by_name.setdefault(str(symbol["name"]), []).append(symbol_node)
                symbol_records.append({
                    "node": symbol_node,
                    "snippet": snippet,
                    "name": str(symbol["name"]),
                    "kind": str(symbol["kind"]),
                    "rel": rel,
                    "references": symbol.get("references"),
                    "ast_based": bool(symbol.get("ast_based")),
                    "ts_based": bool(symbol.get("ts_based")),
                })
                registry_symbols.append({
                    "node_id": symbol_id,
                    "name": str(symbol["name"]),
                    "kind": str(symbol["kind"]),
                    "line_start": symbol["line_start"],
                    "line_end": symbol["line_end"],
                    "references": symbol.get("references"),
                    "ast_based": bool(symbol.get("ast_based")),
                    "ts_based": bool(symbol.get("ts_based")),
                })

            new_registry[rel] = {
                "sha1": content_hash,
                "file_node_id": file_id,
                "symbols": registry_symbols,
            }

        for node in nodes.values():
            if node.node_type == "directory":
                files_here = directory_file_counts.get(node.id, 0)
                child_dirs = sum(
                    1 for child_id in node.children
                    if nodes.get(child_id) and nodes[child_id].node_type == "directory"
                )
                node.summary = f"Directory with {files_here} files and {child_dirs} child directories"
                node.detail_content = self._children_detail(node, nodes)
            elif node.node_type == "project":
                node.detail_content = self._children_detail(node, nodes)

        self._assign_fractal_coordinates(root_node, nodes)
        ordered_nodes = self._ordered_nodes(root_node.id, nodes)
        links = self._build_dependency_links(symbol_records, definitions_by_name, str(root))
        self._populate_embeddings(ordered_nodes, root=str(root))
        self.engine.replace_indexed_nodes(
            ordered_nodes,
            index_root=str(root),
            reset=reset,
            links=links,
        )
        # Persist the registry only after the graph write succeeded, so a crash
        # mid-index never leaves the registry pointing at nodes that aren't in
        # the DB (which would make a later reuse transplant dangling ids).
        registry_all[str(root)] = new_registry
        self._save_file_registry(registry_all)

        stats = IndexStats(
            root=str(root),
            node_count=len(ordered_nodes),
            directory_count=sum(1 for node in ordered_nodes if node.node_type == "directory"),
            file_count=sum(1 for node in ordered_nodes if node.node_type == "file"),
            symbol_count=sum(1 for node in ordered_nodes if node.node_type == "symbol"),
            link_count=len(links),
            skipped_files=skipped_files,
            truncated_files=truncated_files,
            deduped_files=deduped_files,
        )
        # Surface the polyglot-graph health so a dormant call-graph (e.g.
        # tree-sitter-language-pack not installed -> non-Python files silently
        # degrade to symbols-without-edges) is observable, not invisible.
        syntax_links = sum(
            1 for link in links
            if (link.get("type") if isinstance(link, dict)
                else getattr(link, "type", None)) == "syntax_dependency"
        )
        return {
            "status": "indexed",
            "stats": stats.__dict__,
            "reused_files": reused_file_count,
            "treesitter_available": treesitter_extract.available(),
            "syntax_dependency_links": syntax_links,
            "root_node": self.engine._public_node(root_node),
        }

    def zoom_query(self, query: str) -> dict[str, Any]:
        matches = self.search_nodes(query, limit=8)
        if not matches:
            return {"status": "not_found", "query": query, "matches": []}

        best = matches[0]["node"]
        zoom = max(float(best.zoom_threshold) * 1.15, 1.0)
        camera = self.engine.move_camera_and_zoom(best.x, best.y, zoom)
        return {
            "status": "focused",
            "query": query,
            "focused_node": self.engine._public_node(best),
            "score": matches[0]["score"],
            "camera": camera["camera"],
            "fov": camera["fov"],
            "matches": [
                {"score": match["score"], "node": self.engine._public_node(match["node"])}
                for match in matches
            ],
        }

    def get_context(
        self,
        query: str,
        budget: int = DEFAULT_CONTEXT_BUDGET,
        root: str | Path | None = None,
    ) -> dict[str, Any]:
        clean_budget = max(500, int(budget or DEFAULT_CONTEXT_BUDGET))
        scope_root, root_warning = self.engine.resolve_query_root(root)
        warnings = [root_warning] if root_warning else []
        if root_warning and scope_root is None:
            return {
                "query": query,
                "budget": clean_budget,
                "used": 0,
                "selected_count": 0,
                "selected_nodes": [],
                "compiled_context": f"WARNING: {root_warning}",
                "root": None,
                "active_root": self.engine.active_root(),
                "indexed_roots": self.engine.indexed_roots(),
                "warnings": warnings,
            }

        # v0.6.0+hybrid: semantic seed re-rank (graph causal ordering untouched).
        matches = self.search_nodes(query, limit=24, semantic=True, root=scope_root)
        all_nodes = {
            node.id: node for node in self.engine.all_nodes()
            if self._node_in_root(node, scope_root)
        }
        links_by_source: dict[str, list[dict[str, Any]]] = {}
        links_by_target: dict[str, list[dict[str, Any]]] = {}
        for link in self.engine.all_links():
            if link.from_node not in all_nodes or link.to_node not in all_nodes:
                continue
            payload = link.to_dict()
            links_by_source.setdefault(link.from_node, []).append(payload)
            links_by_target.setdefault(link.to_node, []).append(payload)

        candidates: list[tuple[int, DrosteNode, dict[str, Any] | None]] = []
        seen_candidates: set[str] = set()

        def add_candidate(score: int, node: DrosteNode, via: dict[str, Any] | None) -> None:
            if node.id in seen_candidates:
                return
            candidates.append((score, node, via))
            seen_candidates.add(node.id)

        for match in matches:
            node = match["node"]
            base = int(match["score"])
            add_candidate(base, node, None)

            # 1) Causal wormholes first: real syntax_dependency edges, in BOTH
            #    directions, so the budget pays for the callee AND the caller of
            #    the matched symbol before any pure-semantic neighbor. Neighbors
            #    are ordered most-specific-first (smallest scope), so a direct
            #    function caller wins over its large enclosing class.
            callee_links = [
                link for link in links_by_source.get(node.id, [])
                if link.get("type") == "syntax_dependency"
            ]
            caller_links = [
                link for link in links_by_target.get(node.id, [])
                if link.get("type") == "syntax_dependency"
            ]
            callee_links.sort(key=lambda link: self._node_span(all_nodes.get(str(link.get("to", "")))))
            caller_links.sort(key=lambda link: self._node_span(all_nodes.get(str(link.get("from", "")))))
            for link in callee_links:
                callee = all_nodes.get(str(link.get("to", "")))
                if callee:
                    add_candidate(base, callee, {"link": link, "origin": node.id, "role": "callee"})
            for link in caller_links:
                caller = all_nodes.get(str(link.get("from", "")))
                if caller:
                    add_candidate(base, caller, {"link": link, "origin": node.id, "role": "caller"})

            # 2) Remaining (regex/semantic) outgoing edges, lower priority.
            for link in links_by_source.get(node.id, [])[:4]:
                if link.get("type") == "syntax_dependency":
                    continue
                target = all_nodes.get(str(link.get("to", "")))
                if target:
                    add_candidate(max(1, base - 1), target, {"link": link, "origin": node.id, "role": "callee"})

        if len(candidates) > 1:
            focus_candidate = candidates[0]
            neighbor_candidates = candidates[1:]
            neighbor_candidates.sort(
                key=lambda item: (
                    not self._node_contains_critical_lod_signal(item[1]),
                    -item[0],
                    item[1].title,
                )
            )
            candidates = [focus_candidate, *neighbor_candidates]

        terms = self._terms(query)
        sections: list[str] = []
        selected: list[dict[str, Any]] = []
        used = 0

        # v0.5.0 dynamic zoom: derive a per-node char cap orthogonal to the
        # global budget. Wide queries spread the same budget thin (many
        # contracts); detail queries concentrate it (few full bodies). The
        # global guardrail below is untouched — node_budget <= remaining always.
        zoom = self._derive_zoom(query, clean_budget)
        target_breadth = 3 + round(zoom * 9)  # 3 nodes (tight) .. 12 (wide)
        per_node_cap = max(MIN_USEFUL_SECTION, clean_budget // target_breadth)

        for i, (score, node, via) in enumerate(candidates):
            remaining = clean_budget - used
            if remaining < MIN_USEFUL_SECTION:
                break
            is_focus = i == 0

            # The focus node is exempt from the zoom cap so the direct answer is
            # always at full fidelity; everyone else is capped to spread breadth.
            is_critical = self._node_contains_critical_lod_signal(node)
            node_budget = remaining if (is_focus or is_critical or zoom < 0.34) else min(remaining, per_node_cap)

            # Demotion ladder: pick the densest representation of this node that
            # fits `node_budget`. File nodes are decomposed via AST (or the
            # generic brace extractor) into a slim module skeleton instead of
            # being dumped whole; symbol callers collapse to compact stubs. The
            # ONLY node permitted to exceed the cap is a micro focus node.
            fitted = self._fit_section(
                node, via, all_nodes, node_budget, terms, is_focus=is_focus,
            )
            if fitted is None:
                continue
            section, detail_level = fitted

            # HARD GUARDRAIL: _fit_section fits everything to `remaining` except
            # the deliberate micro-focus overflow. This is the invalicable maths.
            if used + len(section) > clean_budget:
                allowed = is_focus and detail_level == "full" and len(section) <= MICRO_FOCUS_CHARS
                if not allowed:
                    continue

            sections.append(section)
            used += len(section)
            selected_item: dict[str, Any] = {
                "score": score,
                "node": self._slim_node(node),
                "detail_level": detail_level,
            }
            if via:
                link = via["link"]
                selected_item["via_wormhole"] = {
                    key: link.get(key) for key in ("from", "to", "type", "label")
                }
                selected_item["wormhole_role"] = via.get("role")
            selected.append(selected_item)
            if used >= clean_budget:
                break

        return {
            "query": query,
            "budget": clean_budget,
            "used": used,
            "selected_count": len(selected),
            "selected_nodes": selected,
            "compiled_context": "\n\n".join(sections),
            "root": scope_root,
            "active_root": self.engine.active_root(),
            "indexed_roots": self.engine.indexed_roots(),
            "warnings": warnings,
        }

    def _derive_zoom(self, query: str, budget: int) -> float:
        """Map query intent to a zoom factor in [0, 1].

        1.0 = wide (many nodes, low fidelity → contracts/skeletons).
        0.0 = tight (few nodes, full bodies). 0.5 = neutral, behaves as before.
        """
        q = query.lower()
        zoom = 0.5
        if any(hint in q for hint in _BROAD_HINTS):
            zoom += 0.35
        if any(hint in q for hint in _DETAIL_HINTS):
            zoom -= 0.35
        if budget >= 9000:  # a generous budget implies the caller wants breadth
            zoom += 0.1
        return max(0.0, min(1.0, zoom))

    def search_nodes(
        self,
        query: str,
        limit: int = 12,
        semantic: bool = False,
        alpha: float = 5.0,
        root: str | Path | None = None,
    ) -> list[dict[str, Any]]:
        """Rank nodes for a query.

        Pure lexical by default (term-overlap scorer). With ``semantic=True`` a
        local embedding cosine is blended on top so concept-intent queries that
        share no tokens with the target ("logic handling unstable network") can
        still surface the right seed. ``score`` stays the integer lexical score
        so downstream causal-wormhole prioritisation in get_context is unchanged;
        only the seed ORDER and the inclusion of semantic-only hits differ.
        Degrades silently to pure lexical when no embeddings are available.
        """
        terms = self._terms(query)
        if not terms:
            return []

        query_embedding: list[float] | None = None
        if semantic:
            try:
                query_embedding = self.engine.projector.embed_text(query)
            except Exception:
                query_embedding = None

        # Two-pass so the lexical score can be normalized before blending.
        # Raw `_score_node` returns unbounded integers (10-40), which dwarfed
        # alpha*cosine (~6*0.7=4): a single token match buried any pure-semantic
        # hit, so the embedding layer was structurally inert no matter how good
        # the vectors were (the A/B that caught this). Normalizing lexical to
        # [0,1] within the candidate set puts a strong semantic match on par
        # with a strong lexical one; agreement (both high) still ranks top, and
        # exact-name lexical hits keep rank 1 (nscore 1.0 + their own high sem).
        scored: list[tuple[int, float, DrosteNode]] = []
        max_score = 0
        scope_root = self.engine.normalize_root(root)
        for node in self.engine.all_nodes():
            if not self._node_in_root(node, scope_root):
                continue
            score = self._score_node(node, terms, query)
            sem = 0.0
            if query_embedding and node.embedding:
                sem = self._cosine(query_embedding, node.embedding)
            # keep lexical hits always; admit semantic-only hits above a floor
            if score > 0 or (query_embedding is not None and sem >= SEMANTIC_MATCH_MIN):
                scored.append((score, sem, node))
                if score > max_score:
                    max_score = score

        matches: list[dict[str, Any]] = []
        for score, sem, node in scored:
            nscore = (score / max_score) if max_score else 0.0
            source_rank = self._source_rank_adjustment(node, query)
            blended = max(0.0, nscore + (alpha * sem if sem > 0 else 0.0) + source_rank)
            matches.append({
                "score": score,
                "semantic": round(sem, 4),
                "source_rank": round(source_rank, 4),
                "blended": round(blended, 4),
                "node": node,
            })

        matches.sort(
            key=lambda match: (
                -match["blended"],
                float(match["node"].zoom_threshold),
                match["node"].title,
            )
        )
        return matches[: max(1, int(limit))]

    def _populate_embeddings(self, nodes: list[DrosteNode], root: str | None = None) -> None:
        """v0.6.0+hybrid: arm the semantic layer by storing one embedding per
        node at index time. Without this the hybrid seed re-rank is inert
        (empty node.embedding -> cosine 0, the dormant-layer bug the eval caught).
        Skips pure structural shells (directory/project) where retrieval never
        lands. Idempotent. Embedding quality scales with the projector backend
        (MiniLM > deterministic hash fallback).

        The text fed to the model is built by `_embedding_text`: title+summary
        alone left the concept-intent layer ~inert (eval CONCEPT LIFT +0.033),
        so for symbols we now embed signature + docstring + simplified path.

        v0.6.2+fast-embed: the embedding pass was the residual bottleneck (one
        ONNX round-trip per node, 3000+ nodes). Two leverages, both transparent
        to callers:
          1. content-hash cache — embedding text is hashed; a vector already
             computed for that exact text (this run, a prior index of any
             project, or a previous index of this one) is reused, so a re-index
             only embeds what actually changed.
          2. batch embedding — every cache MISS is embedded in a single
             vectorised fastembed pass instead of N serial calls."""
        semantic_types = {"symbol", "file", "section", "concept"}
        pending: list[DrosteNode] = []
        texts: list[str] = []
        for node in nodes:
            if node.embedding or node.node_type not in semantic_types:
                continue
            pending.append(node)
            texts.append(self._embedding_text(node, root))
        if not pending:
            return

        cache = self._load_embed_cache()
        misses: list[DrosteNode] = []
        miss_texts: list[str] = []
        miss_keys: list[str] = []
        for node, text in zip(pending, texts):
            key = hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()
            cached = cache.get(key)
            if cached:
                node.embedding = list(cached)
            else:
                misses.append(node)
                miss_texts.append(text)
                miss_keys.append(key)

        if miss_texts:
            try:
                vectors = self.engine.projector.embed_texts(miss_texts)
            except Exception:
                vectors = []
            if len(vectors) != len(misses):
                # batch failed mid-flight -> embed individually, never crash index
                vectors = []
                for text in miss_texts:
                    try:
                        vectors.append(self.engine.projector.embed_text(text))
                    except Exception:
                        vectors.append([])
            for node, key, vector in zip(misses, miss_keys, vectors):
                node.embedding = vector
                if vector:
                    cache[key] = vector
            self._save_embed_cache(cache)

    # ------------------------------------------------------------------
    # v0.6.2+fast-embed: persistent content-hash embedding cache
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # v0.6.3+file-skip: persistent per-file registry (path -> sha1 + node ids)
    # ------------------------------------------------------------------
    def _file_registry_path(self) -> Path:
        db_path = Path(getattr(self.engine, "db_path", "droste_memory_db.json"))
        return db_path.with_name("droste_file_registry.json")

    def _load_file_registry(self) -> dict[str, Any]:
        """All-roots registry: {index_root: {rel_path: {sha1, file_node_id,
        symbols:[...]}}}. Empty/corrupt -> {} (next index rebuilds it)."""
        path = self._file_registry_path()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(raw, dict) or raw.get("version") != INDEX_VERSION:
            return {}
        roots = raw.get("roots")
        return roots if isinstance(roots, dict) else {}

    def _save_file_registry(self, roots: dict[str, Any]) -> None:
        path = self._file_registry_path()
        tmp = path.with_suffix(".json.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(
                json.dumps({"version": INDEX_VERSION, "roots": roots}, ensure_ascii=False),
                encoding="utf-8",
            )
            tmp.replace(path)
        except OSError:
            pass

    def _embed_cache_path(self) -> Path:
        """Cache file beside the engine DB. Path-independent and project-
        independent: keyed only by the embedding text, so identical snippets
        across projects (and re-indexes) reuse the same vector."""
        db_path = Path(getattr(self.engine, "db_path", "droste_memory_db.json"))
        return db_path.with_name("droste_embed_cache.json")

    def _load_embed_cache(self) -> dict[str, list[float]]:
        path = self._embed_cache_path()
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = -1.0
        # Serve the in-RAM copy unless another process advanced the file mtime.
        if self._embed_cache_mem is not None and mtime == self._embed_cache_mtime:
            return self._embed_cache_mem
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._embed_cache_mem = {}
            self._embed_cache_mtime = mtime
            return self._embed_cache_mem
        vectors = raw.get("vectors") if isinstance(raw, dict) else None
        self._embed_cache_mem = vectors if isinstance(vectors, dict) else {}
        self._embed_cache_mtime = mtime
        return self._embed_cache_mem

    def _save_embed_cache(self, cache: dict[str, list[float]]) -> None:
        """Atomic write. Bounded: an unbounded cache would grow with every code
        edit, so beyond the cap the oldest-insertion keys are dropped (dict
        preserves insertion order; reused/added keys are re-appended on save)."""
        MAX_ENTRIES = 50_000
        if len(cache) > MAX_ENTRIES:
            cache = dict(list(cache.items())[-MAX_ENTRIES:])
        path = self._embed_cache_path()
        tmp = path.with_suffix(".json.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(
                json.dumps({"version": 1, "vectors": cache}, ensure_ascii=False),
                encoding="utf-8",
            )
            tmp.replace(path)
            # Keep the memo in sync so the next _load serves RAM, not a re-read.
            self._embed_cache_mem = cache
            try:
                self._embed_cache_mtime = path.stat().st_mtime
            except OSError:
                self._embed_cache_mtime = -1.0
        except OSError:
            pass

    # ------------------------------------------------------------------
    # v0.6.1+dense-embed: rich text for the semantic vector
    # ------------------------------------------------------------------
    def _embedding_text(self, node: DrosteNode, root: str | None = None) -> str:
        """Dense block fed to fastembed so it has something to discriminate.

        Layout (one component per line):
          1. title                       e.g. ``function: draw_diagnostic``
          2. node_type + simplified path e.g. ``symbol in backend/scripts/diagnostic_overlay``
          3. full signature              e.g. ``def draw_diagnostic(frame, ...) -> np.ndarray:``
          4. salient docstring / leading-comment lines (natural language)
        Falls back to the old title+summary when nothing richer is derivable
        (file/section/concept nodes, or unparseable spans)."""
        parts: list[str] = [node.title]

        location = self._simplified_location(node, root)
        if location:
            parts.append(f"{node.node_type} in {location}")

        enriched = False
        if node.node_type == "symbol":
            signature, doc = self._signature_and_doc(node)
            if signature:
                parts.append(signature)
                enriched = True
            if doc:
                parts.append(doc)
                enriched = True

        if not enriched and node.summary:
            parts.append(node.summary)

        return "\n".join(part for part in parts if part and part.strip()).strip()

    def _simplified_location(self, node: DrosteNode, root: str | None = None) -> str:
        """``…/backend/scripts/diagnostic_overlay.py`` -> ``backend/scripts/diagnostic_overlay``.

        Relative to the index root when known; otherwise the last three path
        components. The extension is dropped so the embedding sees only the
        meaningful domain tokens, never the absolute path noise."""
        src = node.source_path or ""
        if not src:
            return ""
        path = Path(src)
        rel: Path | None = None
        if root:
            try:
                rel = path.relative_to(Path(root))
            except ValueError:
                rel = None
        if rel is None:
            tail = path.parts[-3:]
            rel = Path(*tail) if tail else path
        return rel.with_suffix("").as_posix()

    def _signature_and_doc(self, node: DrosteNode) -> tuple[str, str]:
        """(signature, salient docstring/comment) for a symbol; '' each when not
        derivable. Python via AST (real signature + return type + docstring);
        any other language via a brace/colon heuristic + the comment block
        immediately above the symbol."""
        raw = self._raw_source(node)
        if not raw.strip():
            return "", ""

        if (node.source_path or "").lower().endswith(".py"):
            try:
                tree = ast.parse(textwrap.dedent(raw))
            except SyntaxError:
                tree = None
            if tree is not None:
                target = next(
                    (
                        item for item in tree.body
                        if isinstance(item, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                    ),
                    None,
                )
                if target is not None:
                    if isinstance(target, ast.ClassDef):
                        bases = ", ".join(ast.unparse(base) for base in target.bases)
                        signature = f"class {target.name}({bases}):" if bases else f"class {target.name}:"
                    else:
                        kw = "async def" if isinstance(target, ast.AsyncFunctionDef) else "def"
                        ret = f" -> {ast.unparse(target.returns)}" if target.returns is not None else ""
                        signature = f"{kw} {target.name}({ast.unparse(target.args)}){ret}:"
                    doc = self._salient_doc_lines(ast.get_docstring(target) or "")
                    return signature, doc

        return self._generic_signature_and_doc(node, raw)

    def _generic_signature_and_doc(self, node: DrosteNode, raw: str) -> tuple[str, str]:
        """Polyglot fallback: signature up to the opening brace/colon + the
        comment lines immediately above the symbol (// # * /// docstrings)."""
        sig_lines: list[str] = []
        for line in raw.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if "{" in line:
                sig_lines.append(line[: line.index("{")].strip())
                break
            if stripped.endswith(":"):
                sig_lines.append(stripped)
                break
            sig_lines.append(stripped)
            if len(sig_lines) >= 3:
                break
        signature = " ".join(part for part in sig_lines if part).strip()
        return signature, self._leading_comment(node)

    @staticmethod
    def _salient_doc_lines(docstring: str, max_lines: int = 5) -> str:
        """First non-empty lines of a docstring, space-joined and compact."""
        salient = [line.strip() for line in docstring.strip().splitlines() if line.strip()]
        return " ".join(salient[:max_lines])

    def _leading_comment(self, node: DrosteNode, max_lines: int = 4) -> str:
        """Comment block immediately above the symbol's first line (the doc a
        reader sees before the body), cleaned of comment markers."""
        start = int(node.line_start or 0)
        if start <= 1:
            return ""
        try:
            lines = Path(node.source_path or "").read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return ""
        collected: list[str] = []
        index = start - 2  # 0-based line directly above the symbol
        while index >= 0 and len(collected) < max_lines:
            stripped = lines[index].strip()
            if not stripped:
                break
            if stripped.startswith(("//", "#", "*", "/*", "*/", "///", '"""', "'''")):
                cleaned = stripped.lstrip("/*#'\" ").rstrip("*/ ").strip()
                if cleaned:
                    collected.append(cleaned)
                index -= 1
            else:
                break
        return " ".join(reversed(collected))

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        n = min(len(a), len(b))
        if n == 0:
            return 0.0
        dot = 0.0
        na = 0.0
        nb = 0.0
        for i in range(n):
            av = a[i]
            bv = b[i]
            dot += av * bv
            na += av * av
            nb += bv * bv
        if na <= 0.0 or nb <= 0.0:
            return 0.0
        return dot / math.sqrt(na * nb)

    def _collect_files(self, root: Path, max_files: int) -> list[Path]:
        files: list[Path] = []
        for directory, dirnames, filenames in root.walk():
            dirnames[:] = [
                dirname for dirname in sorted(dirnames)
                if dirname not in SKIP_DIRS and not dirname.startswith(".cache")
            ]
            for filename in sorted(filenames):
                if len(files) >= max_files:
                    return files
                path = directory / filename
                if filename in SKIP_FILES:
                    continue
                if path.suffix.lower() not in TEXT_EXTENSIONS:
                    continue
                if self._looks_minified(path):
                    continue
                files.append(path)
        return files

    @staticmethod
    def _looks_minified(path: Path) -> bool:
        """Fix (b): skip generated/minified bundles. By name suffix, or by a
        content sniff (web assets whose first non-empty lines average > 500 chars
        are machine-generated single-line bundles, not hand-written source)."""
        name = path.name.lower()
        if name.endswith(MINIFIED_SUFFIXES):
            return True
        if path.suffix.lower() not in _MINIFY_SNIFF_EXTS:
            return False
        try:
            with open(path, "rb") as handle:
                head = handle.read(8192)
        except OSError:
            return False
        lines = [ln for ln in head.decode("utf-8", errors="replace").splitlines() if ln.strip()][:5]
        if not lines:
            return False
        avg_len = sum(len(ln) for ln in lines) / len(lines)
        return avg_len > _MINIFY_AVG_LINE_LEN

    def _extract_symbols(self, path: Path, text: str) -> list[dict[str, Any]]:
        if path.suffix.lower() == ".py":
            return self._extract_python_symbols(text)
        if path.suffix.lower() in {".md", ".mdx"}:
            return self._extract_markdown_sections(text)
        if path.suffix.lower() == ".sql":
            return self._extract_sql_symbols(text)
        edge_symbols = self._extract_edge_function_symbols(path, text)
        # Tree-sitter path: real AST for non-Python languages, yielding
        # name-based references that become first-class syntax_dependency edges.
        # Returns None (-> fall through to the regex registry below) when
        # tree-sitter is unavailable or the file did not parse into definitions.
        if treesitter_extract.supported_ext(path.suffix.lower()):
            ts_symbols = treesitter_extract.extract_symbols(text, path.suffix.lower())
            if ts_symbols:
                return [*edge_symbols, *ts_symbols]
        if path.suffix.lower() in {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}:
            return [*edge_symbols, *self._extract_javascript_symbols(text)]
        if path.suffix.lower() in {".html", ".css", ".scss"}:
            return self._extract_named_blocks(text)
        spec_lang = EXT_TO_SPEC_LANG.get(path.suffix.lower())
        if spec_lang:
            return [*edge_symbols, *self._extract_by_spec(text, LANG_DEF_SPECS[spec_lang])]
        return edge_symbols

    def _extract_edge_function_symbols(self, path: Path, text: str) -> list[dict[str, Any]]:
        if path.suffix.lower() not in {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}:
            return []
        if not self._is_edge_function_path(str(path)):
            return []
        if path.stem.lower() not in {"index", "main"}:
            return []
        parts = list(path.parts)
        lowered = [part.lower() for part in parts]
        try:
            function_index = lowered.index("functions")
        except ValueError:
            return []
        if function_index + 1 >= len(parts):
            return []
        name = parts[function_index + 1]
        line_count = text.count("\n") + (1 if text else 0)
        return [{
            "kind": "function",
            "name": name,
            "line_start": 1,
            "line_end": max(1, line_count),
            "references": self._extract_references(text),
            "ts_based": True,
        }]

    def _extract_sql_symbols(self, text: str) -> list[dict[str, Any]]:
        matches = list(SQL_OBJECT_PATTERN.finditer(text))
        if not matches:
            return []
        lines = text.splitlines()
        symbols: list[dict[str, Any]] = []
        for index, match in enumerate(matches):
            kind = SQL_OBJECT_KIND.get(match.group(1).upper(), "function")
            name = self._normalise_sql_function_name(match.group(2))
            if not name:
                continue
            next_start = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            start_line = self._line_of_offset(text, match.start())
            end_line = self._find_sql_statement_end_line(text, match.end(), next_start)
            snippet = "\n".join(lines[start_line - 1:end_line])
            references = [
                reference for reference in self._extract_references(snippet)
                if reference != name
            ]
            symbols.append({
                "kind": kind,
                "name": name,
                "line_start": start_line,
                "line_end": max(start_line, end_line),
                "references": references,
                "ast_based": True,
            })
        return symbols

    @staticmethod
    def _normalise_sql_function_name(raw_name: str) -> str:
        name = raw_name.rsplit(".", 1)[-1].strip()
        if len(name) >= 2 and name[0] == '"' and name[-1] == '"':
            name = name[1:-1].replace('""', '"')
        return name

    @staticmethod
    def _line_of_offset(text: str, offset: int) -> int:
        return text.count("\n", 0, max(0, offset)) + 1

    def _find_sql_statement_end_line(self, text: str, start: int, limit: int) -> int:
        limit = max(start, min(limit, len(text)))
        dollar_quote: str | None = None
        index = start
        while index < limit:
            if dollar_quote:
                close_index = text.find(dollar_quote, index, limit)
                if close_index < 0:
                    break
                index = close_index + len(dollar_quote)
                dollar_quote = None
                continue

            match = SQL_DOLLAR_QUOTE_PATTERN.match(text, index)
            if match:
                dollar_quote = match.group(0)
                index = match.end()
                continue

            if text[index] == "'":
                index += 1
                while index < limit:
                    if text[index] == "'" and index + 1 < limit and text[index + 1] == "'":
                        index += 2
                        continue
                    if text[index] == "'":
                        index += 1
                        break
                    index += 1
                continue

            if text[index] == ";":
                return self._line_of_offset(text, index)
            index += 1

        return self._line_of_offset(text, max(start, limit - 1))

    def _extract_by_spec(self, text: str, specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Multi-language definition extraction via the regex registry.

        Block end is computed with the existing brace-balance counter
        (_find_block_end) for brace languages, or a fixed window otherwise.
        First matching spec per line wins, so a `func` line is not also tried
        as a `type`.
        """
        lines = text.splitlines()
        symbols: list[dict[str, Any]] = []
        for index, line in enumerate(lines, start=1):
            for spec in specs:
                match = spec["re"].match(line)
                if not match:
                    continue
                if spec.get("block") == "brace":
                    end = self._find_block_end(lines, index)
                else:
                    end = min(len(lines), index + 24)
                symbols.append({
                    "kind": spec["kind"],
                    "name": match.group(1),
                    "line_start": index,
                    "line_end": end,
                })
                break
        return symbols

    def _extract_python_symbols(self, text: str) -> list[dict[str, Any]]:
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return self._extract_named_blocks(text)

        symbols: list[dict[str, Any]] = []
        for item in ast.walk(tree):
            if isinstance(item, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                kind = "class" if isinstance(item, ast.ClassDef) else "function"
                start = int(getattr(item, "lineno", 1))
                end = int(getattr(item, "end_lineno", start))
                symbols.append({
                    "kind": kind,
                    "name": item.name,
                    "line_start": start,
                    "line_end": end,
                    "references": self._ast_reference_names(item),
                    "ast_based": True,
                })
        symbols.sort(key=lambda symbol: (symbol["line_start"], symbol["name"]))
        return symbols

    @staticmethod
    def _ast_reference_names(scope: ast.AST) -> list[str]:
        """Deterministic call/import/inheritance references inside a def/class scope.

        Walks the AST subtree of one symbol and collects the names it actually
        invokes (``ast.Call``), imports (``ast.Import`` / ``ast.ImportFrom``) or
        inherits from (class bases). This is the syntactic ground truth used to
        build ``syntax_dependency`` edges, as opposed to the regex heuristic.
        """
        names: set[str] = set()
        for child in ast.walk(scope):
            if isinstance(child, ast.Call):
                func = child.func
                if isinstance(func, ast.Name):
                    names.add(func.id)
                elif isinstance(func, ast.Attribute):
                    names.add(func.attr)
            elif isinstance(child, ast.Import):
                for alias in child.names:
                    names.add(alias.name.split(".")[0])
            elif isinstance(child, ast.ImportFrom):
                for alias in child.names:
                    names.add(alias.name)
            elif isinstance(child, ast.ClassDef):
                for base in child.bases:
                    if isinstance(base, ast.Name):
                        names.add(base.id)
                    elif isinstance(base, ast.Attribute):
                        names.add(base.attr)
        return sorted(
            name for name in names
            if name and name.lower() not in REFERENCE_STOPWORDS
        )

    def _extract_javascript_symbols(self, text: str) -> list[dict[str, Any]]:
        lines = text.splitlines()
        symbols: list[dict[str, Any]] = []
        for index, line in enumerate(lines, start=1):
            match = SYMBOL_PATTERN.search(line)
            if not match:
                continue
            name = match.group(1) or match.group(2)
            kind = "class" if "class " in line else "function"
            end = self._find_block_end(lines, index)
            symbols.append({"kind": kind, "name": name, "line_start": index, "line_end": end})
        return symbols

    def _extract_markdown_sections(self, text: str) -> list[dict[str, Any]]:
        lines = text.splitlines()
        headings: list[dict[str, Any]] = []
        for index, line in enumerate(lines, start=1):
            match = HEADING_PATTERN.match(line)
            if match:
                headings.append({
                    "kind": "section",
                    "name": match.group(2).strip(),
                    "line_start": index,
                    "line_end": index,
                    "level": len(match.group(1)),
                })
        for position, heading in enumerate(headings):
            next_start = headings[position + 1]["line_start"] if position + 1 < len(headings) else len(lines) + 1
            heading["line_end"] = max(heading["line_start"], next_start - 1)
            heading.pop("level", None)
        return headings

    def _extract_named_blocks(self, text: str) -> list[dict[str, Any]]:
        lines = text.splitlines()
        symbols: list[dict[str, Any]] = []
        for index, line in enumerate(lines, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith(("def ", "class ", "function ")):
                name = stripped.split("(", 1)[0].replace("def ", "").replace("class ", "").replace("function ", "").strip()
                symbols.append({
                    "kind": "block",
                    "name": name[:80],
                    "line_start": index,
                    "line_end": min(len(lines), index + 24),
                })
        return symbols

    @staticmethod
    def _find_block_end(lines: list[str], start_line: int) -> int:
        brace_balance = 0
        saw_brace = False
        for index in range(start_line, min(len(lines), start_line + 120) + 1):
            line = lines[index - 1]
            brace_balance += line.count("{") - line.count("}")
            saw_brace = saw_brace or "{" in line
            if saw_brace and brace_balance <= 0 and index > start_line:
                return index
        return min(len(lines), start_line + 32)

    def _assign_fractal_coordinates(self, root_node: DrosteNode, nodes: dict[str, DrosteNode]) -> None:
        root_node.x = 0.0
        root_node.y = 0.0
        root_node.semantic_x = 0.0
        root_node.semantic_y = 0.0
        root_node.fixed_x = 0.0
        root_node.fixed_y = 0.0

        def child_sort_key(node: DrosteNode) -> tuple[str, str, str]:
            return (
                node.node_type,
                (node.source_path or node.title).lower(),
                node.id,
            )

        def children_of(parent: DrosteNode, node_type: str) -> list[DrosteNode]:
            return sorted(
                [
                    nodes[child_id]
                    for child_id in parent.children
                    if child_id in nodes and nodes[child_id].node_type == node_type
                ],
                key=child_sort_key,
            )

        def place_on_circle(
            parent: DrosteNode,
            children: list[DrosteNode],
            radius: float,
            start_angle: float = -math.pi / 2.0,
        ) -> None:
            if not children:
                return
            for index, child in enumerate(children):
                angle = start_angle + (2.0 * math.pi * index) / len(children)
                child.x = clamp(parent.x + math.cos(angle) * radius, -1.0, 1.0)
                child.y = clamp(parent.y + math.sin(angle) * radius, -1.0, 1.0)
                child.semantic_x = child.x
                child.semantic_y = child.y
                child.fixed_x = child.x
                child.fixed_y = child.y

        def symbol_radius(count: int) -> float:
            return min(0.052, max(0.018, 0.006 + count * 0.0018))

        def assign_file(file_node: DrosteNode) -> None:
            symbols = children_of(file_node, "symbol")
            place_on_circle(
                file_node,
                symbols,
                radius=symbol_radius(len(symbols)),
                start_angle=-math.pi / 2.0,
            )

        def assign_directory(parent: DrosteNode, is_root: bool = False) -> None:
            directories = children_of(parent, "directory")
            files = children_of(parent, "file")

            directory_radius = 0.18 if is_root else 0.07
            file_radius = 0.095 if is_root else 0.045

            place_on_circle(
                parent,
                directories,
                radius=directory_radius,
                start_angle=-math.pi / 2.0,
            )
            place_on_circle(
                parent,
                files,
                radius=file_radius,
                start_angle=(-math.pi / 2.0) + (math.pi / max(2, len(files))),
            )

            for directory in directories:
                assign_directory(directory)
            for file_node in files:
                assign_file(file_node)

        assign_directory(root_node, is_root=True)

    def _build_dependency_links(
        self,
        symbol_records: list[dict[str, Any]],
        definitions_by_name: dict[str, list[DrosteNode]],
        index_root: str,
    ) -> list[dict[str, Any]]:
        links: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()

        for record in symbol_records:
            if str(record.get("kind")) not in {"class", "function", "block"}:
                continue
            source = record["node"]
            source_lang = _language_of(source.source_path)
            # Prefer deterministic AST references (Python); fall back to the
            # regex heuristic for JS / markdown / syntax-error files.
            if (record.get("ast_based") or record.get("ts_based")) and record.get("references") is not None:
                references = list(record["references"])
                link_type = "syntax_dependency"
            else:
                references = self._extract_references(str(record.get("snippet", "")))
                link_type = "dependency"
            snippet = str(record.get("snippet", ""))
            external_refs = self._extract_external_service_references(snippet)
            rpc_refs = external_refs["rpc"]
            edge_refs = external_refs["edge"]
            table_refs = external_refs["table"]
            db_refs = rpc_refs | table_refs            # both resolve cross-lang into the DB
            if rpc_refs or edge_refs or table_refs:
                references = list(dict.fromkeys([*references, *sorted(db_refs | edge_refs)]))
            for reference in references:
                targets = definitions_by_name.get(reference)
                if not targets:
                    continue
                if reference in db_refs or reference in edge_refs:
                    # DB / edge-function bridge: resolves across language families
                    # (any caller language -> the database object or edge fn).
                    targets = [
                        target for target in targets
                        if self._allows_external_service_link(
                            source_lang,
                            target,
                            rpc=reference in db_refs,
                            edge=reference in edge_refs,
                        )
                    ]
                    edge_type = "syntax_dependency"
                else:
                    # Same-language only: a plain identifier never resolves across
                    # language families, so identically-named symbols in other
                    # languages do not produce phantom wormholes.
                    targets = [
                        target for target in targets
                        if _language_of(target.source_path) == source_lang
                    ]
                    edge_type = link_type
                if not targets:
                    continue
                cross_file_targets = [
                    target for target in targets
                    if target.id != source.id and target.source_path != source.source_path
                ]
                local_targets = [
                    target for target in targets
                    if target.id != source.id and target.source_path == source.source_path
                ]
                for target in [*cross_file_targets, *local_targets][:1]:
                    key = (source.id, target.id, reference)
                    if key in seen:
                        continue
                    seen.add(key)
                    links.append({
                        "from": source.id,
                        "to": target.id,
                        "type": edge_type,
                        "label": reference,
                        "index_root": index_root,
                        "weight": 1.0,
                    })

            # Generic cross-language bridge: a string literal naming a symbol
            # defined in ANOTHER language is a wormhole (handler / channel / RPC
            # / template names). Cross-language only + guarded so same-language
            # identifiers keep flowing through the precise path above untouched.
            bridged = 0
            for token in set(STRING_LITERAL_PATTERN.findall(snippet)):
                if bridged >= MAX_CROSS_LANG_PER_SYMBOL:
                    break
                low = token.lower()
                if token in db_refs or token in edge_refs:
                    continue
                if low in CROSS_LANG_STOPWORDS or low in REFERENCE_STOPWORDS:
                    continue
                for target in definitions_by_name.get(token, []):
                    if target.id == source.id:
                        continue
                    if _language_of(target.source_path) == source_lang:
                        continue                       # cross-language only
                    key = (source.id, target.id, token)
                    if key in seen:
                        continue
                    seen.add(key)
                    links.append({
                        "from": source.id,
                        "to": target.id,
                        "type": "syntax_dependency",
                        "label": token,
                        "index_root": index_root,
                        "weight": 1.0,
                    })
                    bridged += 1
                    break

        return links

    @staticmethod
    def _extract_references(snippet: str) -> list[str]:
        references: set[str] = set()

        for match in CALL_REFERENCE_PATTERN.finditer(snippet):
            name = match.group(1)
            if name.lower() not in REFERENCE_STOPWORDS:
                references.add(name)

        for match in EXTENDS_REFERENCE_PATTERN.finditer(snippet):
            references.add(match.group(1))

        for match in PYTHON_BASE_PATTERN.finditer(snippet):
            for base in re.split(r"[,.\s]+", match.group(1)):
                clean = base.strip()
                if clean and clean.lower() not in REFERENCE_STOPWORDS:
                    references.add(clean)

        return sorted(references)

    @staticmethod
    def _extract_external_service_references(snippet: str) -> dict[str, set[str]]:
        rpc_refs = {
            match.group(1)
            for match in RPC_REFERENCE_PATTERN.finditer(snippet)
        }
        edge_refs: set[str] = set()
        for pattern in EDGE_FUNCTION_REFERENCE_PATTERNS:
            edge_refs.update(match.group(1) for match in pattern.finditer(snippet))
        table_refs: set[str] = set()
        for pattern in DB_TABLE_REFERENCE_PATTERNS:
            for match in pattern.finditer(snippet):
                name = next((g for g in match.groups() if g), None)
                if name and len(name) >= 3 and name.lower() not in CROSS_LANG_STOPWORDS:
                    table_refs.add(name)
        return {"rpc": rpc_refs, "edge": edge_refs, "table": table_refs}

    def _allows_external_service_link(
        self,
        source_lang: str,
        target: DrosteNode,
        *,
        rpc: bool,
        edge: bool,
    ) -> bool:
        # Any caller language may wormhole into the database (rpc/table) or into
        # an edge function — the bridge is defined by the TARGET kind, not the
        # source language, so SQL connects to every language that calls it.
        target_lang = _language_of(target.source_path)
        if rpc and target_lang == "sql":
            return True
        if edge and target_lang == "jsts" and self._is_edge_function_path(target.source_path or ""):
            return True
        return False

    @staticmethod
    def _is_edge_function_path(path: str) -> bool:
        parts = [part.lower() for part in Path(path).parts]
        return "functions" in parts and "supabase" in parts

    @staticmethod
    def _node_span(node: DrosteNode | None) -> int:
        """Line span of a node; smaller = more specific scope (function < class)."""
        if not node or node.line_start is None or node.line_end is None:
            return 10**9
        return max(0, int(node.line_end) - int(node.line_start))

    @staticmethod
    def _wormhole_context_prefix(
        via: dict[str, Any],
        all_nodes: dict[str, DrosteNode],
    ) -> str:
        link = via.get("link", {}) if isinstance(via, dict) else {}
        origin = all_nodes.get(str(via.get("origin", "")))
        origin_title = origin.title if origin else str(via.get("origin", "origin"))
        label = str(link.get("label") or link.get("type") or "dependency")
        link_type = str(link.get("type") or "dependency")
        relation = "called by" if via.get("role") == "caller" else "calls"
        return (
            f"### Wormhole [{link_type}]: {origin_title} {relation} this\n"
            f"via: {label}()"
        )

    def _ordered_nodes(self, root_id: str, nodes: dict[str, DrosteNode]) -> list[DrosteNode]:
        ordered: list[DrosteNode] = []
        seen: set[str] = set()

        def visit(node_id: str) -> None:
            if node_id in seen or node_id not in nodes:
                return
            seen.add(node_id)
            node = nodes[node_id]
            ordered.append(node)
            for child_id in node.children:
                visit(child_id)

        visit(root_id)
        for node_id in sorted(nodes):
            visit(node_id)
        return ordered

    def _context_section(self, node: DrosteNode) -> str:
        header = self._context_header(node)
        body = ""
        if node.source_path and Path(node.source_path).is_file():
            body = self._read_source_excerpt(node)
        if not body:
            body = node.detail_content or node.summary
        if not body:
            return ""
        return f"{header}\n{body.strip()}"

    def _context_header(self, node: DrosteNode) -> str:
        path = node.source_path or "(memory)"
        if node.line_start and node.line_end:
            return f"### {node.title}\nsource: {path}:{node.line_start}-{node.line_end}"
        return f"### {node.title}\nsource: {path}"

    def _read_source_excerpt(self, node: DrosteNode) -> str:
        path = Path(node.source_path or "")
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return node.detail_content

        start = max(1, int(node.line_start or 1))
        end = max(start, int(node.line_end or min(len(lines), start + 60)))
        end = min(end, len(lines))
        return "\n".join(
            f"{line_number:>4}: {lines[line_number - 1]}"
            for line_number in range(start, end + 1)
        )

    @staticmethod
    def _slim_node(node: DrosteNode) -> dict[str, Any]:
        """MCP-facing node payload: identity + location only, no graphics."""
        return {
            "id": node.id,
            "title": node.title,
            "node_type": node.node_type,
            "source_path": node.source_path,
            "line_start": node.line_start,
            "line_end": node.line_end,
        }

    def _raw_source(self, node: DrosteNode) -> str:
        """Raw (unnumbered) source text for the node's line span."""
        path = Path(node.source_path or "")
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return ""
        start = max(1, int(node.line_start or 1))
        end = min(len(lines), int(node.line_end or start))
        if end < start:
            return ""
        return "\n".join(lines[start - 1: end])

    def _contract_section(self, node: DrosteNode) -> str:
        """Header + AST contract for a symbol, or '' if it cannot be derived.

        Only meaningful for Python defs/classes; returns '' otherwise so the
        caller falls back to skipping rather than emitting a broken section.
        """
        if not (node.source_path or "").lower().endswith(".py"):
            return ""
        raw = self._raw_source(node)
        if not raw.strip():
            return ""
        try:
            tree = ast.parse(textwrap.dedent(raw))
        except SyntaxError:
            return ""
        target = next(
            (
                item for item in tree.body
                if isinstance(item, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            ),
            None,
        )
        if target is None:
            return ""
        contract = self._render_contract(target)
        if not contract.strip():
            return ""
        return f"{self._context_header(node)}\n{contract}"

    @staticmethod
    def _render_contract(node: ast.AST) -> str:
        """Signature + first docstring line + self.* assignments + returns."""
        def first_doc(scope: ast.AST) -> str | None:
            doc = ast.get_docstring(scope)
            return doc.strip().splitlines()[0] if doc else None

        if isinstance(node, ast.ClassDef):
            bases = ", ".join(ast.unparse(base) for base in node.bases)
            lines = [f"class {node.name}({bases}):" if bases else f"class {node.name}:"]
            doc = first_doc(node)
            if doc:
                lines.append(f'    """{doc}"""')
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    kw = "async def" if isinstance(item, ast.AsyncFunctionDef) else "def"
                    lines.append(f"    {kw} {item.name}({ast.unparse(item.args)}): ...")
            return "\n".join(lines)

        kw = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
        lines = [f"{kw} {node.name}({ast.unparse(node.args)}):"]
        doc = first_doc(node)
        if doc:
            lines.append(f'    """{doc}"""')

        seen: set[str] = set()
        assigns: list[str] = []
        returns: list[str] = []
        for child in ast.walk(node):
            if isinstance(child, ast.Assign):
                for target in child.targets:
                    if (
                        isinstance(target, ast.Attribute)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == "self"
                    ):
                        line = f"    self.{target.attr} = ..."
                        if line not in seen:
                            seen.add(line)
                            assigns.append(line)
            elif isinstance(child, ast.Return):
                returns.append(f"    {ast.unparse(child)}")
        lines.extend(assigns[:8])
        lines.extend(returns[:6] if returns else ["    ..."])
        return "\n".join(lines)

    def _signature_contract_generic(self, node: DrosteNode) -> str:
        """Language-agnostic contract: signature lines up to the opening brace
        (or trailing ':'), body collapsed to '…'. Works for Go/Rust/TS/Dart and
        any brace/colon language without a parser. Returns '' if not derivable.

        This is the v0.5.0 fallback that lifts the demotion ladder out of its
        Python-only confinement: when ast.parse cannot run, the packer still has
        a [contract] rung to demote to instead of full-or-nothing.
        """
        raw = self._raw_source(node)
        if not raw.strip():
            return ""
        sig_lines: list[str] = []
        for line in raw.splitlines():
            stripped = line.rstrip()
            if "{" in line:  # brace language: signature ends at the first brace
                sig_lines.append(line[: line.index("{") + 1] + " … }")
                break
            if stripped.endswith(":"):  # python-like header
                sig_lines.append(stripped)
                sig_lines.append("    ...")
                break
            sig_lines.append(stripped)
            if len(sig_lines) >= 4:  # long multi-line signature: stop unrolling
                sig_lines.append("    … }")
                break
        if not any(part.strip() for part in sig_lines):
            return ""
        return f"{self._context_header(node)}\n" + "\n".join(sig_lines)

    # ------------------------------------------------------------------
    # v0.4.2+packer-fix: budget-bounded representation selection
    # ------------------------------------------------------------------
    def _node_contains_critical_lod_signal(self, node: DrosteNode) -> bool:
        haystack = "\n".join(
            part for part in (
                node.title,
                node.summary,
                node.detail_content,
                self._raw_source(node) if node.source_path else "",
            )
            if part
        ).lower()
        return any(keyword in haystack for keyword in CRITICAL_LOD_KEYWORDS)

    def _fit_section(
        self,
        node: DrosteNode,
        via: dict[str, Any] | None,
        all_nodes: dict[str, DrosteNode],
        remaining: int,
        terms: list[str],
        is_focus: bool,
    ) -> tuple[str, str] | None:
        """Return (text, detail_level) for the densest representation of `node`
        that fits within `remaining` chars, or None if nothing fits.

        Ladder: full -> caller-stub (callers) -> symbol contract -> file
        skeleton. A micro focus node (full form <= MICRO_FOCUS_CHARS) is the
        sole exception allowed to exceed `remaining`.
        """
        prefix = ""
        if via:
            prefix = self._wormhole_context_prefix(via, all_nodes) + "\n"

        # 1) FULL — verbatim numbered source.
        full_body = self._context_section(node)
        if not full_body.strip():
            return None
        full = prefix + full_body
        if len(full) <= remaining:
            return full, "full"

        is_critical = self._node_contains_critical_lod_signal(node)
        if is_critical:
            if is_focus and len(full) <= MICRO_FOCUS_CHARS:
                return full, "full"
            return None

        is_file = node.node_type == "file"

        # 2) CALLER STUB — external callers must land as compact, distinct
        #    contracts (<= CALLER_STUB_CHARS), never dragging in a file body.
        if via and via.get("role") == "caller" and not is_file:
            stub = self._caller_stub(node, prefix)
            if stub and len(stub) <= remaining:
                return stub, "contract"

        # 3) SYMBOL CONTRACT — signature + docstring + self.* + returns.
        #    Skipped for file nodes: _contract_section would pick only the
        #    first top-level def and misrepresent the module.
        if not is_file:
            # Python AST contract first (richest); generic brace/colon contract
            # as the v0.5.0 fallback for Go/Rust/Dart and AST-failing files.
            contract_body = self._contract_section(node) or self._signature_contract_generic(node)
            if contract_body.strip():
                tag = (prefix.rstrip("\n") + " [contract]\n") if prefix else ""
                # _contract_section already embeds its own header; avoid dup.
                contract = tag + contract_body if prefix else contract_body
                if len(contract) <= remaining:
                    return contract, "contract"

        # 4) FILE SKELETON — focus symbol full, every other top-level def/class
        #    demoted to a one-line contract, fitted hard to `remaining`.
        if is_file:
            head = (prefix.rstrip("\n") + " [skeleton]\n") if prefix else ""
            skel_body = self._skeleton_section(node, terms, remaining - len(head))
            if skel_body:
                skel = head + skel_body
                if len(skel) <= remaining:
                    return skel, "skeleton"

        # 5) MICRO FOCUS exception — a tiny answer is always shown whole even if
        #    it nudges over a small budget. Giant nodes never reach here.
        if is_focus and len(full) <= MICRO_FOCUS_CHARS:
            return full, "full"

        return None

    def _skeleton_section(
        self, node: DrosteNode, terms: list[str], max_chars: int
    ) -> str:
        """Render a .py FILE as a slim module skeleton: the query-focus symbol
        in full, every other top-level def/class as a one-line contract.

        Guaranteed to return text whose length <= max_chars, or '' if even the
        minimal skeleton cannot fit.
        """
        if max_chars <= 0:
            return ""
        path = node.source_path or ""
        if not path.lower().endswith(".py"):
            return self._skeleton_section_generic(node, terms, max_chars)
        try:
            src = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        try:
            tree = ast.parse(src)
        except SyntaxError:
            # Syntax-error .py: no AST, but the generic brace extractor returns
            # '' too (no spec for .py), so fall through to an empty skeleton.
            return self._skeleton_section_generic(node, terms, max_chars)

        tops = [
            n for n in tree.body
            if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        if not tops:
            return ""

        src_lines = src.splitlines()
        termset = {t.lower() for t in (terms or []) if t}

        def is_focus_sym(n: ast.AST) -> bool:
            name = getattr(n, "name", "").lower()
            return bool(name) and (name in termset or any(t in name for t in termset))

        def numbered(n: ast.AST) -> str:
            start = max(1, int(getattr(n, "lineno", 1)))
            end = min(len(src_lines), int(getattr(n, "end_lineno", start)))
            return "\n".join(
                f"{ln:>4}: {src_lines[ln - 1]}" for ln in range(start, end + 1)
            )

        focus_syms = [n for n in tops if is_focus_sym(n)]
        rest = [n for n in tops if n not in focus_syms]
        contracts = {n: self._render_contract(n) for n in tops}

        header = self._context_header(node)
        banner = "# --- module skeleton: focus symbol full, rest as contracts ---"
        focus_parts = [numbered(n) for n in focus_syms]
        rest_parts = [contracts[n] for n in rest]

        def assemble() -> str:
            return "\n".join([header, banner] + focus_parts + rest_parts)

        out = assemble()
        # Drop trailing non-focus contracts until it fits.
        while rest_parts and len(out) > max_chars:
            rest_parts.pop()
            out = assemble()
        if len(out) <= max_chars:
            return out

        # Focus body itself too large: demote focus to its contract.
        focus_parts = [contracts[n] for n in focus_syms] or [contracts[tops[0]]]
        rest_parts = []
        out = assemble()
        if len(out) <= max_chars:
            return out
        return ""

    def _skeleton_section_generic(
        self, node: DrosteNode, terms: list[str], max_chars: int
    ) -> str:
        """Brace-language counterpart of _skeleton_section for Go/Rust/Dart.

        Renders a FILE as a slim skeleton: the query-focus symbol numbered in
        full, every other top-level definition collapsed to a one-line
        signature contract. Guaranteed <= max_chars, or '' if nothing fits.
        """
        if max_chars <= 0:
            return ""
        path = node.source_path or ""
        spec_lang = EXT_TO_SPEC_LANG.get(Path(path).suffix.lower())
        if not spec_lang:
            return ""
        try:
            src = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        symbols = self._extract_by_spec(src, LANG_DEF_SPECS[spec_lang])
        if not symbols:
            return ""

        src_lines = src.splitlines()
        termset = {t.lower() for t in (terms or []) if t}

        def is_focus_sym(sym: dict[str, Any]) -> bool:
            name = str(sym["name"]).lower()
            return bool(name) and (name in termset or any(t in name for t in termset))

        def numbered(sym: dict[str, Any]) -> str:
            start = max(1, int(sym["line_start"]))
            end = min(len(src_lines), int(sym["line_end"]))
            return "\n".join(
                f"{ln:>4}: {src_lines[ln - 1]}" for ln in range(start, end + 1)
            )

        def contract_of(sym: dict[str, Any]) -> str:
            start = max(1, int(sym["line_start"]))
            line = src_lines[start - 1].strip() if start - 1 < len(src_lines) else ""
            if "{" in line:
                line = line[: line.index("{") + 1] + " … }"
            return f"{sym['kind']} {sym['name']}  →  {line}"

        focus_syms = [s for s in symbols if is_focus_sym(s)]
        rest = [s for s in symbols if s not in focus_syms]

        header = self._context_header(node)
        banner = "# --- module skeleton (generic): focus symbol full, rest as contracts ---"
        focus_parts = [numbered(s) for s in focus_syms]
        rest_parts = [contract_of(s) for s in rest]

        def assemble() -> str:
            return "\n".join([header, banner] + focus_parts + rest_parts)

        out = assemble()
        while rest_parts and len(out) > max_chars:
            rest_parts.pop()
            out = assemble()
        if len(out) <= max_chars:
            return out

        # Focus body itself too large: demote focus to its one-line contract.
        focus_parts = [contract_of(s) for s in focus_syms] or [contract_of(symbols[0])]
        rest_parts = []
        out = assemble()
        if len(out) <= max_chars:
            return out
        return ""

    def _caller_stub(self, node: DrosteNode, prefix: str) -> str:
        """Compact, distinct contract stub for an external caller, hard-capped
        at CALLER_STUB_CHARS so callers stay visible as their own entries."""
        loc = node.source_path or "(memory)"
        if node.line_start and node.line_end:
            loc = f"{loc}:{node.line_start}-{node.line_end}"
        sig = self._signature_line(node)
        body = f"### {node.title}\nsource: {loc}"
        if sig:
            body += f"\n{sig}"
        head = (prefix.rstrip("\n") + " [caller-contract]\n") if prefix else ""
        return (head + body)[:CALLER_STUB_CHARS]

    def _signature_line(self, node: DrosteNode) -> str:
        """One-line signature for a Python def/class, or '' if not derivable."""
        if not (node.source_path or "").lower().endswith(".py"):
            return ""
        raw = self._raw_source(node)
        if not raw.strip():
            return ""
        try:
            tree = ast.parse(textwrap.dedent(raw))
        except SyntaxError:
            return ""
        target = next(
            (
                item for item in tree.body
                if isinstance(item, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            ),
            None,
        )
        if target is None:
            return ""
        if isinstance(target, ast.ClassDef):
            return f"class {target.name}: ..."
        kw = "async def" if isinstance(target, ast.AsyncFunctionDef) else "def"
        return f"{kw} {target.name}({ast.unparse(target.args)}): ..."

    def _score_node(self, node: DrosteNode, terms: list[str], query: str) -> int:
        title = node.title.lower()
        path = (node.source_path or "").lower()
        haystack = " ".join([
            node.title,
            node.summary,
            node.detail_content[:1200],
            node.node_type,
            path,
        ]).lower()
        score = 0
        for term in terms:
            if term in title:
                score += 10
            if term in path:
                score += 6
            if term in haystack:
                score += 2
        if query.lower() in title:
            score += 20
        if query.lower() in path:
            score += 12
        if node.node_type == "symbol":
            score += 3
        elif node.node_type == "file":
            score += 2
        return score

    def _source_rank_adjustment(self, node: DrosteNode, query: str) -> float:
        query_terms = set(self._terms(query))
        if query_terms & _TEST_DOC_QUERY_HINTS:
            return 0.0

        path = "/" + (node.source_path or "").replace("\\", "/").lower().lstrip("/")
        adjustment = 0.0
        if any(marker in path for marker in _RUNTIME_PATH_MARKERS):
            adjustment += 0.08
        if (
            any(marker in path for marker in _TEST_DOC_PATH_MARKERS)
            or path.endswith("/readme.md")
            or path.endswith("/readme.mdx")
        ):
            adjustment -= 0.10
        return adjustment

    def _node_in_root(self, node: DrosteNode, root: str | Path | None) -> bool:
        scope = self.engine.normalize_root(root)
        if scope is None:
            return True
        return self.engine.normalize_root(node.index_root) == scope

    @staticmethod
    def _terms(text: str) -> list[str]:
        return [
            term for term in re.findall(r"[A-Za-z0-9_.$/-]+", text.lower())
            if len(term) >= 2
        ]

    @staticmethod
    def _stable_id(kind: str, key: str, salt: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", f"{kind}-{key}".lower()).strip("-")[:52]
        slug = slug or kind
        digest = hashlib.sha1(f"{kind}|{key}|{salt}".encode("utf-8")).hexdigest()[:10]
        return f"{slug}-{digest}"

    @staticmethod
    def _relative_path(path: Path, root: Path) -> str:
        try:
            return path.relative_to(root).as_posix() or "."
        except ValueError:
            return path.as_posix()

    @staticmethod
    def _path_depth(rel_path: str) -> int:
        return 0 if rel_path == "." else len(Path(rel_path).parts)

    @staticmethod
    def _slice_lines(text: str, start: int, end: int, max_chars: int = 3500) -> str:
        lines = text.splitlines()
        clean_start = max(1, start)
        clean_end = min(len(lines), max(clean_start, end))
        snippet = "\n".join(lines[clean_start - 1: clean_end])
        if len(snippet) > max_chars:
            return snippet[:max_chars].rstrip() + "\n[snippet truncated]"
        return snippet

    def _file_detail(self, rel: str, text: str, symbols: Iterable[dict[str, Any]], truncated: bool) -> str:
        symbol_lines = [
            f"- {symbol['kind']} {symbol['name']}:{symbol['line_start']}-{symbol['line_end']}"
            for symbol in list(symbols)[:30]
        ]
        preview = self._slice_lines(text, 1, min(36, len(text.splitlines())), max_chars=2600)
        suffix = "\n[file truncated during indexing]" if truncated else ""
        return f"File: {rel}\nSymbols:\n" + "\n".join(symbol_lines) + f"\n\nPreview:\n{preview}{suffix}"

    @staticmethod
    def _children_detail(node: DrosteNode, nodes: dict[str, DrosteNode]) -> str:
        lines = [f"{node.node_type}: {node.title}", "Children:"]
        for child_id in node.children[:80]:
            child = nodes.get(child_id)
            if child:
                lines.append(f"- {child.node_type}: {child.title}")
        if len(node.children) > 80:
            lines.append(f"- ... {len(node.children) - 80} more")
        return "\n".join(lines)

    # ---- Reactive / elastic incremental ingest (0.4.0+reactive) -----------

    def ingest_file_incremental(self, path: str | Path) -> dict[str, Any]:
        """Surgically (re)index a single file into the live in-RAM graph.

        Never re-runs the full project: parses one file's AST, appends its
        nodes on a deterministic radial coordinate that touches zero existing
        nodes, wires its outgoing syntax_dependency edges to already-indexed
        symbols, and atomically persists via the owning engine.
        """
        fp = Path(path).expanduser().resolve()
        if fp.suffix.lower() not in TEXT_EXTENSIONS:
            return {"status": "skipped", "reason": "unsupported_ext", "path": str(fp)}

        nodes = self.engine.all_nodes()
        root = self._owning_index_root(fp, nodes)
        if not root:
            return {"status": "skipped", "reason": "outside_indexed_roots", "path": str(fp)}
        root_path = Path(root).resolve()
        rel = self._relative_path(fp, root_path)
        if any(part in SKIP_DIRS for part in Path(rel).parts):
            return {"status": "skipped", "reason": "ignored_dir", "path": str(fp)}

        try:
            raw = fp.read_bytes()
        except OSError:
            return {"status": "error", "reason": "unreadable", "path": str(fp)}
        if b"\x00" in raw or len(raw) > DEFAULT_MAX_FILE_BYTES:
            return {"status": "skipped", "reason": "binary_or_too_large", "path": str(fp)}
        text = raw.decode("utf-8", errors="replace")
        content_hash = hashlib.sha1(raw).hexdigest()
        line_count = text.count("\n") + (1 if text else 0)
        now = utc_now()

        by_id: dict[str, DrosteNode] = {node.id: node for node in nodes}
        # NB: symbol nodes share their file's source_path, so they must be
        # excluded here or they would shadow the file/dir node for a path.
        by_path: dict[str, DrosteNode] = {
            node.source_path: node for node in nodes
            if node.source_path and node.node_type != "symbol"
        }

        # MODIFY: drop the prior version of this exact file (+ its symbols).
        removed_ids: set[str] = set()
        prior = by_path.get(str(fp))
        if prior is not None and prior.node_type == "file":
            removed_ids.add(prior.id)
            removed_ids.update(prior.children)

        created_dirs: list[DrosteNode] = []
        attachments: dict[str, str] = {}
        parent = self._ensure_directory_chain(
            fp.parent, root_path, by_path, by_id, created_dirs, attachments
        )
        if parent is None:
            return {"status": "skipped", "reason": "no_parent_chain", "path": str(fp)}

        file_radius = 0.095 if parent.node_type == "project" else 0.045
        sibling_files = [
            node for node in by_id.values()
            if node.parent_id == parent.id
            and node.node_type == "file"
            and node.id not in removed_ids
        ]
        fx, fy = self._append_radial_coordinate(parent, sibling_files, file_radius)
        depth = self._path_depth(rel)

        symbols = self._extract_symbols(fp, text)[:400]
        file_id = self._stable_id("file", rel, content_hash)
        file_node = DrosteNode(
            id=file_id,
            title=f"file: {rel}",
            summary=f"{fp.suffix or 'text'} file, {line_count} lines, {len(symbols)} indexed symbols",
            detail_content=self._file_detail(rel, text, symbols, truncated=False),
            node_type="file",
            parent_id=parent.id,
            source_path=str(fp),
            line_start=1,
            line_end=line_count,
            index_root=str(root_path),
            content_hash=content_hash,
            x=fx, y=fy, semantic_x=fx, semantic_y=fy, fixed_x=fx, fixed_y=fy,
            zoom_threshold=7.0 + depth * 1.5,
            created_at=now, updated_at=now,
        )
        attachments[file_id] = parent.id

        symbol_nodes: list[DrosteNode] = []
        symbol_records: list[dict[str, Any]] = []
        for symbol in symbols:
            symbol_id = self._stable_id(
                "symbol", rel, f"{symbol['kind']}:{symbol['name']}:{symbol['line_start']}"
            )
            snippet = self._slice_lines(text, symbol["line_start"], symbol["line_end"])
            sym = DrosteNode(
                id=symbol_id,
                title=f"{symbol['kind']}: {symbol['name']}",
                summary=f"{symbol['kind']} in {rel}:{symbol['line_start']}-{symbol['line_end']}",
                detail_content=snippet,
                node_type="symbol",
                parent_id=file_id,
                source_path=str(fp),
                line_start=symbol["line_start"],
                line_end=symbol["line_end"],
                index_root=str(root_path),
                content_hash=hashlib.sha1(snippet.encode("utf-8", errors="replace")).hexdigest(),
                zoom_threshold=14.0 + depth * 1.5,
                created_at=now, updated_at=now,
            )
            symbol_nodes.append(sym)
            file_node.children.append(symbol_id)
            symbol_records.append({
                "node": sym,
                "snippet": snippet,
                "name": str(symbol["name"]),
                "kind": str(symbol["kind"]),
                "references": symbol.get("references"),
                "ast_based": bool(symbol.get("ast_based")),
                "ts_based": bool(symbol.get("ts_based")),
            })

        self._place_symbols_even(file_node, symbol_nodes)

        # Outgoing syntax_dependency edges into the existing global graph.
        existing_symbols = [
            node for node in nodes
            if node.node_type == "symbol" and node.id not in removed_ids
        ]
        def_index = self._definition_index(existing_symbols, symbol_nodes)
        links = self._incremental_links(symbol_records, def_index, str(root_path))

        result = self.engine.upsert_file(
            new_nodes=[*created_dirs, file_node, *symbol_nodes],
            new_links=links,
            removed_node_ids=removed_ids,
            attachments=attachments,
        )
        return {
            "status": "modified" if prior is not None else "created",
            "path": str(fp),
            "rel": rel,
            "coordinate": {"x": fx, "y": fy},
            "symbols": len(symbol_nodes),
            "new_dirs": len(created_dirs),
            "outgoing_links": len(links),
            "graph": result,
        }

    @staticmethod
    def _owning_index_root(fp: Path, nodes: list[DrosteNode]) -> str | None:
        best: str | None = None
        best_len = -1
        seen: set[str] = set()
        for node in nodes:
            root = node.index_root
            if not root or root in seen:
                continue
            seen.add(root)
            try:
                root_path = Path(root).resolve()
                fp.relative_to(root_path)
            except (OSError, ValueError):
                continue
            length = len(str(root_path))
            if length > best_len:
                best_len = length
                best = str(root_path)
        return best

    @staticmethod
    def _append_radial_coordinate(
        parent: DrosteNode,
        siblings: list[DrosteNode],
        radius: float,
    ) -> tuple[float, float]:
        """Bisect the largest angular gap among siblings on the parent's ring.

        Deterministic, append-only: the returned coordinate never requires
        moving any existing sibling. First child lands at the top (-pi/2).
        """
        if not siblings:
            theta = -math.pi / 2.0
        else:
            angles = sorted(
                math.atan2(sib.y - parent.y, sib.x - parent.x) for sib in siblings
            )
            if len(angles) == 1:
                theta = angles[0] + math.pi
            else:
                best_gap = -1.0
                theta = -math.pi / 2.0
                count = len(angles)
                for index in range(count):
                    a = angles[index]
                    b = angles[(index + 1) % count]
                    gap = (b - a) % (2.0 * math.pi)
                    if gap > best_gap:
                        best_gap = gap
                        theta = a + gap / 2.0
            theta = (theta + math.pi) % (2.0 * math.pi) - math.pi
        x = clamp(parent.x + math.cos(theta) * radius, -1.0, 1.0)
        y = clamp(parent.y + math.sin(theta) * radius, -1.0, 1.0)
        return x, y

    @staticmethod
    def _place_symbols_even(file_node: DrosteNode, symbol_nodes: list[DrosteNode]) -> None:
        count = len(symbol_nodes)
        if not count:
            return
        radius = min(0.052, max(0.018, 0.006 + count * 0.0018))
        start = -math.pi / 2.0
        for index, sym in enumerate(symbol_nodes):
            angle = start + (2.0 * math.pi * index) / count
            sym.x = clamp(file_node.x + math.cos(angle) * radius, -1.0, 1.0)
            sym.y = clamp(file_node.y + math.sin(angle) * radius, -1.0, 1.0)
            sym.semantic_x = sym.x
            sym.semantic_y = sym.y
            sym.fixed_x = sym.x
            sym.fixed_y = sym.y

    def _ensure_directory_chain(
        self,
        directory: Path,
        root_path: Path,
        by_path: dict[str, DrosteNode],
        by_id: dict[str, DrosteNode],
        created: list[DrosteNode],
        attachments: dict[str, str],
    ) -> DrosteNode | None:
        dpath = directory.resolve()
        existing = by_path.get(str(dpath))
        if existing is not None:
            return existing
        if dpath == root_path:
            return None  # root must already exist as a project node
        parent = self._ensure_directory_chain(
            dpath.parent, root_path, by_path, by_id, created, attachments
        )
        if parent is None:
            return None
        dir_radius = 0.18 if parent.node_type == "project" else 0.07
        siblings = [
            node for node in by_id.values()
            if node.parent_id == parent.id and node.node_type == "directory"
        ]
        x, y = self._append_radial_coordinate(parent, siblings, dir_radius)
        rel = self._relative_path(dpath, root_path)
        depth = len(Path(rel).parts)
        node = DrosteNode(
            id=self._stable_id("directory", rel, str(root_path)),
            title=f"dir: {rel}",
            summary=f"Directory {rel}",
            detail_content=f"Directory: {rel}\nParent: {parent.title}",
            node_type="directory",
            parent_id=parent.id,
            source_path=str(dpath),
            index_root=str(root_path),
            x=x, y=y, semantic_x=x, semantic_y=y, fixed_x=x, fixed_y=y,
            zoom_threshold=2.0 + depth * 1.5,
            created_at=utc_now(), updated_at=utc_now(),
        )
        by_id[node.id] = node
        by_path[str(dpath)] = node
        created.append(node)
        attachments[node.id] = parent.id
        return node

    @staticmethod
    def _symbol_name(node: DrosteNode) -> str | None:
        title = node.title or ""
        return title.split(": ", 1)[1].strip() if ": " in title else None

    def _definition_index(
        self,
        existing_symbols: list[DrosteNode],
        new_symbols: list[DrosteNode],
    ) -> dict[str, list[DrosteNode]]:
        index: dict[str, list[DrosteNode]] = {}
        for node in [*existing_symbols, *new_symbols]:
            name = self._symbol_name(node)
            if name:
                index.setdefault(name, []).append(node)
        return index

    def _incremental_links(
        self,
        symbol_records: list[dict[str, Any]],
        def_index: dict[str, list[DrosteNode]],
        index_root: str,
    ) -> list[dict[str, Any]]:
        links: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for record in symbol_records:
            source = record["node"]
            source_lang = _language_of(source.source_path)
            if (record.get("ast_based") or record.get("ts_based")) and record.get("references") is not None:
                references = list(record["references"])
                link_type = "syntax_dependency"
            else:
                references = self._extract_references(str(record.get("snippet", "")))
                link_type = "dependency"
            external_refs = self._extract_external_service_references(str(record.get("snippet", "")))
            rpc_refs = external_refs["rpc"]
            edge_refs = external_refs["edge"]
            if rpc_refs or edge_refs:
                references = list(dict.fromkeys([*references, *sorted(rpc_refs | edge_refs)]))
            for reference in references:
                targets = def_index.get(reference)
                if not targets:
                    continue
                if reference in rpc_refs or reference in edge_refs:
                    targets = [
                        t for t in targets
                        if self._allows_external_service_link(
                            source_lang,
                            t,
                            rpc=reference in rpc_refs,
                            edge=reference in edge_refs,
                        )
                    ]
                    edge_type = "syntax_dependency"
                else:
                    targets = [
                        t for t in targets if _language_of(t.source_path) == source_lang
                    ]
                    edge_type = link_type
                if not targets:
                    continue
                cross = [
                    t for t in targets
                    if t.id != source.id and t.source_path != source.source_path
                ]
                local = [
                    t for t in targets
                    if t.id != source.id and t.source_path == source.source_path
                ]
                for target in [*cross, *local][:1]:
                    key = (source.id, target.id, reference)
                    if key in seen:
                        continue
                    seen.add(key)
                    links.append({
                        "from": source.id,
                        "to": target.id,
                        "type": edge_type,
                        "label": reference,
                        "index_root": index_root,
                        "weight": 1.0,
                    })
        return links


def index_project(
    path: str | Path,
    *,
    engine: DrosteConceptEngine | None = None,
    reset: bool = False,
    max_files: int = DEFAULT_MAX_FILES,
    max_symbols: int = DEFAULT_MAX_SYMBOLS,
    max_symbols_per_lang: int = DEFAULT_MAX_SYMBOLS_PER_LANG,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
) -> dict[str, Any]:
    return DrosteProjectIngester(engine).index_project(
        path,
        reset=reset,
        max_files=max_files,
        max_symbols=max_symbols,
        max_symbols_per_lang=max_symbols_per_lang,
        max_file_bytes=max_file_bytes,
    )


def zoom_query(
    query: str,
    *,
    engine: DrosteConceptEngine | None = None,
) -> dict[str, Any]:
    return DrosteProjectIngester(engine).zoom_query(query)


def droste_zoom_query(
    query: str,
    *,
    engine: DrosteConceptEngine | None = None,
) -> dict[str, Any]:
    return zoom_query(query, engine=engine)


def get_context(
    query: str,
    *,
    engine: DrosteConceptEngine | None = None,
    budget: int = DEFAULT_CONTEXT_BUDGET,
    root: str | Path | None = None,
) -> dict[str, Any]:
    return DrosteProjectIngester(engine).get_context(query, budget=budget, root=root)
