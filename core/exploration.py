"""Progressive graph exploration with a budget on the complete text response.

Receipts acknowledge content retained by the caller, not a model's attention.
Only an exact representation of unchanged source can be reused. No session
content or acknowledgement is stored by the server.
"""
from __future__ import annotations

import ast
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
import textwrap
from typing import Any

from .droste_engine import DrosteConceptEngine
from .droste_ingester import DrosteProjectIngester
from .inspection import _public_symbols, check_index


ENCODINGS = ("o200k_base", "cl100k_base")
LEVELS = ("map", "structure", "source")


@lru_cache(maxsize=2)
def tokenizer(name: str = "o200k_base"):
    if name not in ENCODINGS:
        raise ValueError("encoding must be o200k_base or cl100k_base")
    try:
        import truststore
        truststore.inject_into_ssl()
        import tiktoken
        return tiktoken.get_encoding(name)
    except Exception as exc:
        raise ValueError("Cannot load the token vocabulary. Install droste-memory and run "
                         "'droste explore --prepare-tokenizers' with network access once; "
                         "cached vocabularies work offline.") from exc


def encode_packet(packet: dict[str, Any], encoder) -> str:
    """Count the exact compact JSON text, including its final newline and used field."""
    for _ in range(8):
        text = json.dumps(packet, ensure_ascii=False, separators=(",", ":")) + "\n"
        actual = len(encoder.encode_ordinary(text))
        if packet.get("used") == actual:
            return text
        packet["used"] = actual
    raise RuntimeError("Token accounting did not converge")


def _structure(raw: str, kind: str) -> str:
    tree = None
    try:
        tree = ast.parse(textwrap.dedent(raw))
        node = next((n for n in tree.body if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))), None)
    except SyntaxError:
        node = None
    if node is not None:
        def signature(item):
            if isinstance(item, ast.ClassDef):
                bases = ", ".join(ast.unparse(b) for b in item.bases)
                return f"class {item.name}({bases}):" if bases else f"class {item.name}:"
            prefix = "async def" if isinstance(item, ast.AsyncFunctionDef) else "def"
            return f"{prefix} {item.name}({ast.unparse(item.args)}):"
        if kind == "file":
            definitions = [n for n in tree.body if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))]
            lines = [signature(n) + " ..." for n in definitions[:20]]
            if len(definitions) > 20:
                lines.append(f"# {len(definitions) - 20} more top-level definitions omitted")
            lines.append("# module structure only; implementations omitted")
            return "\n".join(lines)
        lines = [signature(node)]
        doc = ast.get_docstring(node)
        if doc:
            lines.append("    " + json.dumps(doc.splitlines()[0], ensure_ascii=False))
        if isinstance(node, ast.ClassDef):
            methods = [n for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            lines.extend("    " + signature(n) + " ..." for n in methods[:12])
            if len(methods) > 12:
                lines.append(f"    # {len(methods) - 12} more methods omitted")
        lines.append("    ... # implementation omitted; zoom to source")
        return "\n".join(lines)
    if kind == "symbol":
        # Keep whole lines. This is a signature hint, never a claimed body.
        first = next((line.strip() for line in raw.splitlines() if line.strip()), "")
        return first + "\n[implementation omitted]" if first else ""
    return ""


def explore(engine: DrosteConceptEngine, query: str = "project", *, tokens: int = 2000,
            level: str = "map", focus: str | None = None, root: str | None = None,
            encoding: str = "o200k_base", seen: list[str] | None = None) -> str:
    """Return one text block <= tokens, including JSON, locators and receipts.

    map -> structure -> source changes representation, never truncates code.
    focus accepts an indexed node ID or a unique exact qualified symbol name.
    seen contains handles for representations the caller STILL retains. Clear
    it after compaction or session reset. Graph completeness is not guaranteed.
    """
    if not isinstance(tokens, int) or isinstance(tokens, bool) or not 256 <= tokens <= 64000:
        raise ValueError("tokens must be an integer from 256 to 64000")
    if level not in LEVELS:
        raise ValueError("level must be map, structure or source")
    if seen is not None and (not isinstance(seen, list) or len(seen) > 1000 or
                            any(not isinstance(h, str) or not re.fullmatch(r"[a-f0-9]{32}", h) for h in seen)):
        raise ValueError("seen must contain at most 1000 content handles returned by explore")
    encoder = tokenizer(encoding)
    packet: dict[str, Any] = {"v": 1, "budget": tokens, "used": 0, "encoding": encoding,
                              "level": level, "status": "ok", "health": "unverified",
                              "items": [], "links": [], "links_truncated": False, "reused": [], "omitted": 0,
                              "warnings": ["static_graph_may_miss_dynamic_references"]}

    def fits():
        return len(encoder.encode_ordinary(encode_packet(packet, encoder))) <= tokens

    def finish():
        text = encode_packet(packet, encoder)
        if len(encoder.encode_ordinary(text)) > tokens:
            raise RuntimeError("Exploration response exceeds its token budget")
        return text

    scope, root_warning = engine.resolve_query_root(root)
    if root_warning:
        packet.update(status="select_root")
        packet["warnings"].append("select_an_indexed_root_explicitly")
        return finish()
    health = check_index(engine, root=scope, verify_content=True)
    packet["health"] = health["status"]
    if health["status"] not in {"fresh", "partial"}:
        packet.update(status="needs_index")
        packet["warnings"].append("reindex_before_exploring")
        return finish()
    if health["status"] == "partial":
        packet["warnings"].append("index_coverage_is_partial")
    state = engine.index_state(scope)
    nodes = {n.id: n for n in engine.all_nodes() if engine.normalize_root(n.index_root) == scope}
    public = _public_symbols([n for n in nodes.values() if n.node_type == "symbol"], scope)
    ingester = DrosteProjectIngester(engine)
    seeds = []
    if focus:
        matches = [n for n in nodes.values() if n.id == focus or
                   (n.id in public and focus in {public[n.id]["name"], public[n.id]["qualified_name"]})]
        if len(matches) != 1:
            packet["status"] = "ambiguous" if matches else "not_found"
            packet["warnings"].append("use_find_or_map_then_repeat_with_an_exact_id")
            # Candidates themselves are useful navigation items, packed below.
            seeds = matches
        else:
            seeds = matches
    elif query.strip().lower() in {"", "project"}:
        seeds = [n for n in nodes.values() if n.node_type == "project"][:1]
    else:
        semantic = state.get("embedding_model") == engine.projector.identity and engine.projector.backend != "hash"
        if not semantic:
            packet["warnings"].append("lexical_search_only")
        seeds = [m["node"] for m in ingester.search_nodes(query, semantic=semantic, root=scope, limit=12)]
        if semantic and state.get("embedding_model") != engine.projector.identity:
            packet["warnings"].append("lexical_search_only")
    if not seeds:
        packet["status"] = "not_found"
        return finish()
    origin = seeds[0] if packet["status"] == "ok" else None
    if origin:
        packet["focus"] = origin.id

    all_links = [link for link in engine.all_links() if link.from_node in nodes and link.to_node in nodes]
    candidates = []
    candidate_ids = set()

    def add(node_id):
        if node_id in nodes and node_id not in candidate_ids:
            candidates.append(nodes[node_id])
            candidate_ids.add(node_id)

    if origin:
        add(origin.id)
        for child in origin.children:
            add(child)
        for link in all_links:
            if link.from_node == origin.id:
                add(link.to_node)
            elif link.to_node == origin.id:
                add(link.from_node)
    for node in seeds:
        add(node.id)
    packet["omitted"] = len(candidates)
    if len(candidates) > 200:
        packet["warnings"].append("candidate_limit_200")
    acknowledged = set(seen or [])
    source_cache = {}
    accepted = {}

    for node in candidates[:200]:
        path = Path(node.source_path) if node.source_path else None
        relative = ""
        if path:
            try:
                relative = path.relative_to(scope).as_posix()
            except ValueError:
                # Do not expose unscoped paths from manually injected nodes.
                continue
        source = ""
        digest = ""
        if relative in state["files"]:
            if relative not in source_cache:
                try:
                    data = path.read_bytes()
                    source_cache[relative] = (hashlib.sha256(data).hexdigest(), data.decode("utf-8", errors="replace").splitlines())
                except OSError:
                    source_cache[relative] = ("", [])
            digest, lines = source_cache[relative]
            if digest != state["files"][relative]["sha256"]:
                # A writer changed the file after check_index. Do not reuse old
                # spans/receipts for newly changed source, even with --seen.
                packet["warnings"] = ["source_changed_during_request", "reindex_before_exploring"]
                packet.update(status="needs_index", health="stale", items=[], links=[], reused=[], omitted=len(candidates))
                return finish()
            start = max(1, int(node.line_start or 1))
            end = min(len(lines), int(node.line_end or len(lines)))
            source = "\n".join(f"{i}: {lines[i - 1]}" for i in range(start, end + 1))
            raw = "\n".join(lines[start - 1:end])
        else:
            raw = ""
        name = public.get(node.id, {}).get("qualified_name", node.title.split(": ", 1)[-1])
        base = {"id": node.id, "name": name, "kind": node.node_type, "path": relative}
        if node.line_start:
            base["lines"] = [node.line_start, node.line_end or node.line_start]
        if node.children:
            base["children"] = len(node.children)
        requested_level = level if origin and node.id == origin.id else ("map" if level == "map" else "structure")
        variants = ["source", "structure", "map"] if requested_level == "source" else (["structure", "map"] if requested_level == "structure" else ["map"])
        # Ambiguity candidates are always maps, never arbitrary source choices.
        if packet["status"] != "ok":
            variants = ["map"]
        for actual_level in variants:
            item = {**base, "level": actual_level}
            if actual_level == "source":
                if not source:
                    continue
                item["text"] = source
            elif actual_level == "structure":
                contract = _structure(raw, node.node_type)
                if not contract:
                    continue
                item["text"] = contract
            canonical = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            item["handle"] = hashlib.sha256((scope + "\0" + digest + "\0" + canonical).encode()).hexdigest()[:32]
            reused = item["handle"] in acknowledged
            destination = packet["reused"] if reused else packet["items"]
            destination.append({"id": node.id, "level": actual_level, "handle": item["handle"]} if reused else item)
            packet["omitted"] -= 1
            if fits():
                accepted[node.id] = len(packet["items"]) - 1 if not reused else None
                break
            destination.pop()
            packet["omitted"] += 1
        if origin and node.id == origin.id and node.id not in accepted:
            packet["status"] = "budget_too_small"
            # Avoid returning unrelated neighbors as if the requested focus fit.
            return finish()

    # Links reference item positions instead of repeating long IDs. References
    # involving acknowledged items remain discoverable with the references tool.
    seen_edges = set()
    for link in all_links:
        a, b = accepted.get(link.from_node), accepted.get(link.to_node)
        if a is None or b is None or (a, b) in seen_edges:
            continue
        seen_edges.add((a, b))
        packet["links"].append([a, b, link.resolution or "legacy_unverified"])
        if not fits():
            packet["links"].pop()
            packet["links_truncated"] = True
            break
    return finish()
