"""Export the REAL Droste code graph (nodes + causal edges) for the cockpit.

The cockpit's fractal orbits/arcs need the full project structure and the
syntax_dependency edges — data that `status --json` (counts only) and
`context --json` (budget-scoped subset) don't carry. This dumps the live graph
of one indexed root into `visualizer/graph.json`, shaped for cockpit.html.

Usage:
    python visualizer/export_graph.py [--root <indexed root path>] [--out <file>]

With no --root it picks the most recently indexed root from the file registry.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.droste_engine import DrosteConceptEngine

_LANG_BY_EXT = {
    ".dart": "dart", ".py": "py", ".js": "js", ".ts": "ts", ".tsx": "tsx",
    ".jsx": "jsx", ".sql": "sql", ".yaml": "yaml", ".yml": "yaml",
    ".json": "json", ".html": "html", ".css": "css", ".md": "md",
    ".sh": "sh", ".go": "go", ".rs": "rs", ".java": "java", ".kt": "kt",
}

_RISK_TERMS = {
    "todo": 1,
    "fixme": 2,
    "hack": 2,
    "deprecated": 2,
    "unsafe": 3,
    "exception": 1,
    "error": 1,
    "mock": 1,
    "secret": 3,
    "token": 1,
}


def _norm(path: str | None) -> str:
    return (path or "").replace("\\", "/")


def _label(title: str) -> str:
    # Titles look like "class: KpiCard", "def: foo", "function: bar".
    if ": " in title:
        return title.split(": ", 1)[1].strip()
    return title.strip()


def _lang(path: str | None) -> str:
    suffix = Path(path).suffix.lower() if path else ""
    return _LANG_BY_EXT.get(suffix, "txt")


def _risk_score(*parts: str | None) -> int:
    text = " ".join(part or "" for part in parts).lower()
    return min(5, sum(weight for term, weight in _RISK_TERMS.items() if term in text))


def _resolve_root(p: str) -> str:
    return _norm(str(Path(p).expanduser().resolve())).rstrip("/").lower()


def _pick_root(engine: DrosteConceptEngine, requested: str | None) -> str:
    nodes = [n for n in engine.all_nodes() if getattr(n, "index_root", None)]
    roots = {n.index_root for n in nodes}
    if not roots:
        raise SystemExit("no indexed roots found — run `droste index <path>` first")
    if requested:
        want = _resolve_root(requested)        # resolves ".", relative paths, ~
        for r in roots:
            if _resolve_root(r) == want:
                return r
        raise SystemExit("root not indexed: " + str(requested)
                         + "\nindexed roots:\n  " + "\n  ".join(sorted(roots)))
    # No root requested: pick the MOST RECENTLY indexed (by node timestamp),
    # not the alphabetically-last one.
    latest: dict[str, str] = {}
    for n in nodes:
        ts = (getattr(n, "updated_at", "") or getattr(n, "created_at", "") or "")
        if ts > latest.get(n.index_root, ""):
            latest[n.index_root] = ts
    return max(latest, key=latest.get) if latest else sorted(roots)[-1]


def export(requested_root: str | None, out_path: Path) -> dict:
    engine = DrosteConceptEngine()
    root = _pick_root(engine, requested_root)
    root_norm = _norm(root).rstrip("/")

    nodes = [n for n in engine.all_nodes() if _norm(getattr(n, "index_root", "")).rstrip("/") == root_norm]
    keep_ids = {n.id for n in nodes}

    out_nodes = []
    node_langs: dict[str, str] = {}
    for n in nodes:
        d = n.to_dict()
        detail = d.get("detail_content") or ""
        lang = _lang(d.get("source_path"))
        node_langs[n.id] = lang
        out_nodes.append({
            "id": n.id,
            "type": n.node_type,                       # project | directory | file | symbol
            "title": d.get("title") or "",
            "label": _label(d.get("title") or n.id),
            "parent": d.get("parent_id"),
            "path": _norm(d.get("source_path")),
            "rel": _norm(d.get("source_path")).replace(root_norm + "/", "") if d.get("source_path") else "",
            "line_start": d.get("line_start"),
            "line_end": d.get("line_end"),
            "lang": lang,
            "summary": (d.get("summary") or "")[:400],
            "detail": detail[:1800],
            "risk_score": _risk_score(
                d.get("title"),
                d.get("summary"),
                detail[:1800],
            ),
        })

    out_edges = []
    for link in engine.all_links():
        ld = link.to_dict()
        f, t = ld.get("from"), ld.get("to")
        if f in keep_ids and t in keep_ids and ld.get("type") == "syntax_dependency":
            out_edges.append({
                "from": f,
                "to": t,
                "type": "syntax_dependency",
                "label": ld.get("label") or "",
                "via_wormhole": True,
                "cross_language": node_langs.get(f) != node_langs.get(t),
            })

    risk_by_id = {node["id"]: node["risk_score"] for node in out_nodes}
    parent_by_id = {node["id"]: node.get("parent") for node in out_nodes}
    for node in out_nodes:
        if not node["risk_score"]:
            continue
        parent = parent_by_id.get(node["id"])
        while parent:
            risk_by_id[parent] = max(risk_by_id.get(parent, 0), node["risk_score"])
            parent = parent_by_id.get(parent)
    for node in out_nodes:
        node["risk_score"] = risk_by_id.get(node["id"], 0)

    counts = {
        "project": sum(1 for n in out_nodes if n["type"] == "project"),
        "directory": sum(1 for n in out_nodes if n["type"] == "directory"),
        "file": sum(1 for n in out_nodes if n["type"] == "file"),
        "symbol": sum(1 for n in out_nodes if n["type"] == "symbol"),
        "edge": len(out_edges),
    }
    payload = {
        "root": root,
        "root_name": Path(root).name,
        "counts": counts,
        "nodes": out_nodes,
        "edges": out_edges,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description="Export the live Droste graph for the cockpit.")
    parser.add_argument("--root", default=None, help="Indexed root path (default: most recent).")
    parser.add_argument("--out", default=str(Path(__file__).resolve().parent / "graph.json"))
    args = parser.parse_args()
    counts = export(args.root, Path(args.out))
    print(f"graph.json written -> {args.out}")
    print(f"  project:{counts['project']} dirs:{counts['directory']} "
          f"files:{counts['file']} symbols:{counts['symbol']} edges:{counts['edge']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
