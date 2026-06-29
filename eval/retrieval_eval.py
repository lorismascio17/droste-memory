"""Droste-Memory retrieval evaluation harness (v1).

The number Droste was missing. Runs fully offline against the live JSON graph
(local-core, never the stale visualizer), stdlib-only so it honours the
zero-config moat.

Core idea — self-supervised ground truth
----------------------------------------
The AST `syntax_dependency` edges ARE the causal truth: if A calls B, then a
developer asking about B should be shown A (its caller) and B's callees. We do
not hand-label anything. For each probed symbol we derive its gold set straight
from the graph and measure how much of that causal neighbourhood each retrieval
system recovers.

Two systems under test, at EQUAL retrieval breadth k
----------------------------------------------------
  * droste    : full engine — lexical score + bidirectional wormhole traversal
                (ingester.get_context). This is the product.
  * lexical   : pure term-overlap ranking (ingester.search_nodes), NO graph
                expansion. This is the Aider-tags / cosine-RAG analogue: it can
                only return what *names match*, never a caller that shares no
                tokens with the query.

The headline metric is the GRAPH LIFT: how many true causal neighbours Droste
recovers that the lexical baseline structurally cannot. That is the falsifiable
evidence for "graph beats vectors on structural intent".

Usage
-----
    python eval/retrieval_eval.py --budget 6000 --sample 80 --seed 7
    python eval/retrieval_eval.py --db PATH --json out.json
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
_LOCAL_PKGS = ROOT / ".python-packages"
if _LOCAL_PKGS.exists() and str(_LOCAL_PKGS) not in sys.path:
    sys.path.insert(0, str(_LOCAL_PKGS))

from core.droste_engine import DEFAULT_DB_PATH, DrosteConceptEngine  # noqa: E402
from core.droste_ingester import DrosteProjectIngester  # noqa: E402

SYNTAX_EDGE = "syntax_dependency"
# Symbol names shared by more than this many distinct nodes are too ambiguous to
# attribute a single gold set to (e.g. `main`, `__init__`) and are skipped.
MAX_NAME_COLLISION = 3


def symbol_name(title: str) -> str:
    """`function: draw_diagnostic` -> `draw_diagnostic` (the realistic query)."""
    return title.split(":", 1)[-1].strip() if ":" in title else title.strip()


def build_gold_sets(engine: DrosteConceptEngine) -> tuple[
    dict[str, set[str]], dict[str, Any], dict[str, int]
]:
    """For each symbol with causal edges, gold = {self} U direct callers/callees."""
    nodes = {n.id: n for n in engine.all_nodes()}
    callers: dict[str, set[str]] = defaultdict(set)  # B -> {A : A calls B}
    callees: dict[str, set[str]] = defaultdict(set)  # A -> {B : A calls B}
    for link in engine.all_links():
        if link.type != SYNTAX_EDGE:
            continue
        if link.from_node in nodes and link.to_node in nodes:
            callees[link.from_node].add(link.to_node)
            callers[link.to_node].add(link.from_node)

    name_counts: dict[str, int] = defaultdict(int)
    for node in nodes.values():
        if node.node_type == "symbol":
            name_counts[symbol_name(node.title).lower()] += 1

    gold: dict[str, set[str]] = {}
    for nid, node in nodes.items():
        if node.node_type != "symbol":
            continue
        neighbours = callers.get(nid, set()) | callees.get(nid, set())
        if not neighbours:
            continue
        if name_counts[symbol_name(node.title).lower()] > MAX_NAME_COLLISION:
            continue
        gold[nid] = {nid} | neighbours
    return gold, nodes, name_counts


def droste_ids(result: dict[str, Any]) -> list[str]:
    return [item["node"]["id"] for item in result.get("selected_nodes", [])]


def lexical_ids(ingester: DrosteProjectIngester, query: str, k: int) -> list[str]:
    return [m["node"].id for m in ingester.search_nodes(query, limit=k)]


def recall(retrieved: list[str], gold: set[str]) -> float:
    if not gold:
        return 0.0
    return len(set(retrieved) & gold) / len(gold)


def rank_of(retrieved: list[str], target: str) -> int | None:
    for i, nid in enumerate(retrieved, start=1):
        if nid == target:
            return i
    return None


def evaluate(
    db_path: Path, budget: int, sample: int, seed: int
) -> dict[str, Any]:
    engine = DrosteConceptEngine(db_path)
    ingester = DrosteProjectIngester(engine)
    gold, nodes, _ = build_gold_sets(engine)

    probe_ids = sorted(gold.keys())
    rng = random.Random(seed)
    rng.shuffle(probe_ids)
    if sample > 0:
        probe_ids = probe_ids[:sample]

    rows: list[dict[str, Any]] = []
    agg = {
        "droste_neighbor_recall": 0.0,
        "lexical_neighbor_recall": 0.0,
        "droste_def_mrr": 0.0,
        "lexical_def_mrr": 0.0,
        "droste_def_hit@1": 0,
        "graph_only_neighbors": 0,  # gold neighbours Droste got that lexical missed
    }

    for nid in probe_ids:
        node = nodes[nid]
        query = symbol_name(node.title)
        gold_set = gold[nid]
        neighbours = gold_set - {nid}

        d_result = ingester.get_context(query, budget=budget)
        d_ids = droste_ids(d_result)
        k = max(len(d_ids), 1)  # fair: lexical gets the same retrieval breadth
        l_ids = lexical_ids(ingester, query, k)

        d_neigh = recall(d_ids, neighbours) if neighbours else None
        l_neigh = recall(l_ids, neighbours) if neighbours else None
        d_rank = rank_of(d_ids, nid)
        l_rank = rank_of(l_ids, nid)
        graph_only = (set(d_ids) & neighbours) - set(l_ids)

        if d_neigh is not None:
            agg["droste_neighbor_recall"] += d_neigh
            agg["lexical_neighbor_recall"] += l_neigh
        agg["droste_def_mrr"] += (1.0 / d_rank) if d_rank else 0.0
        agg["lexical_def_mrr"] += (1.0 / l_rank) if l_rank else 0.0
        agg["droste_def_hit@1"] += 1 if d_rank == 1 else 0
        agg["graph_only_neighbors"] += len(graph_only)

        rows.append({
            "query": query,
            "node_id": nid,
            "gold_neighbors": len(neighbours),
            "k": k,
            "droste_neighbor_recall": d_neigh,
            "lexical_neighbor_recall": l_neigh,
            "droste_def_rank": d_rank,
            "lexical_def_rank": l_rank,
            "graph_only_neighbors": sorted(graph_only),
        })

    n = len(probe_ids) or 1
    n_neigh = sum(1 for r in rows if r["gold_neighbors"] > 0) or 1
    summary = {
        "db": str(db_path),
        "budget": budget,
        "probes": len(probe_ids),
        "total_symbols_with_edges": len(gold),
        "droste_neighbor_recall": round(agg["droste_neighbor_recall"] / n_neigh, 4),
        "lexical_neighbor_recall": round(agg["lexical_neighbor_recall"] / n_neigh, 4),
        "graph_lift": round(
            (agg["droste_neighbor_recall"] - agg["lexical_neighbor_recall"]) / n_neigh, 4
        ),
        "droste_def_mrr": round(agg["droste_def_mrr"] / n, 4),
        "lexical_def_mrr": round(agg["lexical_def_mrr"] / n, 4),
        "droste_def_hit@1_rate": round(agg["droste_def_hit@1"] / n, 4),
        "graph_only_neighbors_total": agg["graph_only_neighbors"],
    }
    return {"summary": summary, "rows": rows}


def strip_name(summary: str, name: str) -> str:
    """Remove the literal identifier so the query is pure concept-intent."""
    import re
    out = re.sub(re.escape(name), " ", summary, flags=re.IGNORECASE)
    return " ".join(out.split())


def evaluate_concept(
    db_path: Path, sample: int, seed: int, k: int = 10
) -> dict[str, Any]:
    """Concept-intent task: query = NL summary (name stripped), gold = {node}.

    Isolates the front where pure-graph/lexical is weakest. Measures whether the
    hybrid embedding seed re-rank recovers the right node when tokens alone fall
    short. Lift here is bounded by the embedding backend present (MiniLM > hash).
    """
    engine = DrosteConceptEngine(db_path)
    ingester = DrosteProjectIngester(engine)
    nodes = {n.id: n for n in engine.all_nodes()}

    probes = []
    for nid, node in nodes.items():
        if node.node_type != "symbol" or not node.summary:
            continue
        query = strip_name(node.summary, symbol_name(node.title))
        if len(query.split()) < 3:
            continue
        probes.append((nid, query))
    probes.sort()
    rng = random.Random(seed)
    rng.shuffle(probes)
    if sample > 0:
        probes = probes[:sample]

    agg = {"lex_mrr": 0.0, "hyb_mrr": 0.0, "lex_hit5": 0, "hyb_hit5": 0,
           "semantic_only_recoveries": 0}
    for nid, query in probes:
        lex = [m["node"].id for m in ingester.search_nodes(query, limit=k, semantic=False)]
        hyb = [m["node"].id for m in ingester.search_nodes(query, limit=k, semantic=True)]
        lr = rank_of(lex, nid)
        hr = rank_of(hyb, nid)
        agg["lex_mrr"] += (1.0 / lr) if lr else 0.0
        agg["hyb_mrr"] += (1.0 / hr) if hr else 0.0
        agg["lex_hit5"] += 1 if (lr and lr <= 5) else 0
        agg["hyb_hit5"] += 1 if (hr and hr <= 5) else 0
        if (hr and hr <= k) and not (lr and lr <= k):
            agg["semantic_only_recoveries"] += 1

    n = len(probes) or 1
    return {"summary": {
        "db": str(db_path), "probes": len(probes), "k": k,
        "lexical_concept_mrr": round(agg["lex_mrr"] / n, 4),
        "hybrid_concept_mrr": round(agg["hyb_mrr"] / n, 4),
        "concept_mrr_lift": round((agg["hyb_mrr"] - agg["lex_mrr"]) / n, 4),
        "lexical_hit@5": round(agg["lex_hit5"] / n, 4),
        "hybrid_hit@5": round(agg["hyb_hit5"] / n, 4),
        "semantic_only_recoveries": agg["semantic_only_recoveries"],
    }}


def main() -> int:
    parser = argparse.ArgumentParser(description="Droste retrieval eval harness")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--budget", type=int, default=6000)
    parser.add_argument("--sample", type=int, default=80,
                        help="max probes (0 = all symbols with causal edges)")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--json", default="")
    parser.add_argument("--concept", action="store_true",
                        help="run the concept-intent task (hybrid vs lexical seed)")
    args = parser.parse_args()

    if args.concept:
        report = evaluate_concept(Path(args.db), args.sample, args.seed)
        s = report["summary"]
        print("=" * 64)
        print("DROSTE CONCEPT-INTENT EVAL  —  hybrid vs lexical seed")
        print("=" * 64)
        print(f"probes                   {s['probes']}  (k={s['k']})")
        print("-" * 64)
        print(f"concept MRR    lexical   {s['lexical_concept_mrr']:.3f}")
        print(f"concept MRR    hybrid    {s['hybrid_concept_mrr']:.3f}")
        print(f"CONCEPT LIFT             {s['concept_mrr_lift']:+.3f}   "
              f"(+{s['semantic_only_recoveries']} nodes only hybrid recovered)")
        print(f"hit@5          lexical   {s['lexical_hit@5']:.3f}")
        print(f"hit@5          hybrid    {s['hybrid_hit@5']:.3f}")
        print("=" * 64)
        if args.json:
            Path(args.json).write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(f"wrote {args.json}")
        return 0

    report = evaluate(Path(args.db), args.budget, args.sample, args.seed)
    s = report["summary"]

    print("=" * 64)
    print("DROSTE RETRIEVAL EVAL  —  graph vs lexical baseline")
    print("=" * 64)
    print(f"db                       {s['db']}")
    print(f"probes / total w/ edges  {s['probes']} / {s['total_symbols_with_edges']}")
    print(f"budget                   {s['budget']}")
    print("-" * 64)
    print(f"neighbor-recall  droste  {s['droste_neighbor_recall']:.3f}")
    print(f"neighbor-recall  lexical {s['lexical_neighbor_recall']:.3f}")
    print(f"GRAPH LIFT               {s['graph_lift']:+.3f}   "
          f"(+{s['graph_only_neighbors_total']} causal nodes lexical missed)")
    print("-" * 64)
    print(f"def MRR          droste  {s['droste_def_mrr']:.3f}")
    print(f"def MRR          lexical {s['lexical_def_mrr']:.3f}")
    print(f"def hit@1        droste  {s['droste_def_hit@1_rate']:.3f}")
    print("=" * 64)

    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
