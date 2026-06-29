"""Comparative retrieval eval — Droste vs the retrieval *cores* of the market.

Honest scope
------------
We cannot run Cursor / Sourcegraph Cody / Continue here (closed products,
their own infra). What we CAN run — faithfully and reproducibly — are the two
retrieval ALGORITHMS those products are built on, at equal retrieval breadth k:

  * lexical    : pure term-overlap ranking. The ctags / ripgrep / Aider
                 repo-map analogue. Can only return what *name-matches*.
  * embedding  : pure cosine over real fastembed (BAAI/bge-small-en-v1.5, 384d)
                 vectors, NO graph. The vanilla vector-RAG analogue — the core
                 of Cody/Continue/Cursor "semantic" code search and of any
                 Pinecone/Weaviate + embeddings stack.
  * droste     : the product — lexical+semantic seed, then bidirectional
                 syntax_dependency wormhole traversal (get_context).

So this is a *retrieval-method* benchmark, not a product benchmark: the
competitor products layer UX + an LLM on top of one of the first two cores.
The claim under test is narrow and falsifiable: graph-causal traversal recovers
structural intent that neither a lexical nor a pure-vector core can reach.

Ground truth is self-supervised (no hand labels): the AST syntax_dependency
edges. For a probed symbol, gold = {self} U direct callers U direct callees.

Two fronts:
  STRUCTURAL  query = symbol name        gold = causal neighbours
  CONCEPT     query = NL docstring       gold = {the symbol itself}
              (identifier stripped)

Real embeddings are required; the script aborts if the projector falls back to
the deterministic hash backend (that would make the embedding baseline a lie).

Usage:
    python eval/comparative_eval.py
    python eval/comparative_eval.py --repos PATH1 PATH2 --sample 60 --max-files 250
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.droste_engine import DrosteConceptEngine          # noqa: E402
from core.droste_ingester import DrosteProjectIngester      # noqa: E402

SYNTAX_EDGE = "syntax_dependency"
MAX_NAME_COLLISION = 3

# Pass target repos with --repos PATH1 PATH2 ...; defaults to the current dir.
DEFAULT_REPOS = ["."]


def log(m: str = "") -> None:
    print(m, flush=True)


def symbol_name(title: str) -> str:
    return title.split(":", 1)[-1].strip() if ":" in title else title.strip()


def strip_name(summary: str, name: str) -> str:
    import re
    out = re.sub(re.escape(name), " ", summary, flags=re.IGNORECASE)
    return " ".join(out.split())


def build_gold_sets(engine: DrosteConceptEngine):
    nodes = {n.id: n for n in engine.all_nodes()}
    callers: dict[str, set[str]] = defaultdict(set)
    callees: dict[str, set[str]] = defaultdict(set)
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
        neigh = callers.get(nid, set()) | callees.get(nid, set())
        if not neigh:
            continue
        if name_counts[symbol_name(node.title).lower()] > MAX_NAME_COLLISION:
            continue
        gold[nid] = {nid} | neigh
    return gold, nodes


# --- ranking systems --------------------------------------------------------
def droste_ids(result: dict[str, Any]) -> list[str]:
    return [item["node"]["id"] for item in result.get("selected_nodes", [])]


def lexical_ids(ing: DrosteProjectIngester, query: str, k: int) -> list[str]:
    return [m["node"].id for m in ing.search_nodes(query, limit=k, semantic=False)]


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def embedding_ids(ing: DrosteProjectIngester, all_symbols: list, query: str, k: int) -> list[str]:
    """Pure-vector baseline: cosine(query, node.embedding), top-k. No lexical,
    no graph — the faithful vanilla vector-RAG core."""
    qv = ing.engine.projector.embed_text(query)
    scored = [(_cosine(qv, n.embedding), n.id) for n in all_symbols if n.embedding]
    scored.sort(key=lambda t: -t[0])
    return [nid for _, nid in scored[:k]]


# --- metrics ----------------------------------------------------------------
def recall(retrieved: list[str], gold: set[str]) -> float:
    return (len(set(retrieved) & gold) / len(gold)) if gold else 0.0


def rank_of(retrieved: list[str], target: str) -> int | None:
    for i, nid in enumerate(retrieved, 1):
        if nid == target:
            return i
    return None


def ndcg(retrieved: list[str], gold: set[str]) -> float:
    if not gold:
        return 0.0
    dcg = sum(1.0 / math.log2(i + 1) for i, nid in enumerate(retrieved, 1) if nid in gold)
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, len(gold) + 1))
    return dcg / idcg if idcg else 0.0


def eval_repo(path: Path, sample: int, seed: int, budget: int, max_files: int) -> dict[str, Any]:
    workdir = Path(tempfile.mkdtemp(prefix="droste_cmp_"))
    db = workdir / "db.json"
    eng = DrosteConceptEngine(db_path=db)          # real fastembed backend
    ing = DrosteProjectIngester(eng)
    backend = eng.projector.backend
    if backend == "hash":
        raise RuntimeError("embedding backend resolved to hash; real embeddings required")

    t0 = time.perf_counter()
    res = ing.index_project(str(path), reset=True, max_files=max_files,
                            max_symbols=40_000, max_symbols_per_lang=20_000)
    index_s = time.perf_counter() - t0
    stats = res["stats"]

    gold, nodes = build_gold_sets(eng)
    all_symbols = [n for n in eng.all_nodes() if n.node_type == "symbol"]

    rng = random.Random(seed)

    # STRUCTURAL front -------------------------------------------------------
    struct_ids = sorted(gold.keys())
    rng.shuffle(struct_ids)
    struct_ids = struct_ids[:sample] if sample > 0 else struct_ids
    S = {"droste_nr": 0.0, "lex_nr": 0.0, "emb_nr": 0.0,
         "droste_ndcg": 0.0, "lex_ndcg": 0.0, "emb_ndcg": 0.0,
         "graph_only": 0, "n": 0}
    for nid in struct_ids:
        node = nodes[nid]
        query = symbol_name(node.title)
        neigh = gold[nid] - {nid}
        if not neigh:
            continue
        d = droste_ids(ing.get_context(query, budget=budget))
        k = max(len(d), 1)
        l = lexical_ids(ing, query, k)
        e = embedding_ids(ing, all_symbols, query, k)
        S["droste_nr"] += recall(d, neigh); S["lex_nr"] += recall(l, neigh); S["emb_nr"] += recall(e, neigh)
        S["droste_ndcg"] += ndcg(d, neigh); S["lex_ndcg"] += ndcg(l, neigh); S["emb_ndcg"] += ndcg(e, neigh)
        S["graph_only"] += len((set(d) & neigh) - set(l) - set(e))
        S["n"] += 1

    # CONCEPT front ----------------------------------------------------------
    concept_probes = []
    for nid, node in nodes.items():
        if node.node_type != "symbol" or not node.summary:
            continue
        q = strip_name(node.summary, symbol_name(node.title))
        if len(q.split()) < 4:
            continue
        concept_probes.append((nid, q))
    concept_probes.sort()
    rng.shuffle(concept_probes)
    concept_probes = concept_probes[:sample] if sample > 0 else concept_probes
    C = {"droste_mrr": 0.0, "lex_mrr": 0.0, "emb_mrr": 0.0,
         "droste_hit5": 0, "lex_hit5": 0, "emb_hit5": 0, "n": 0}
    for nid, query in concept_probes:
        d = droste_ids(ing.get_context(query, budget=budget))
        k = max(len(d), 10)
        l = lexical_ids(ing, query, k)
        e = embedding_ids(ing, all_symbols, query, k)
        for sys_key, ids in (("droste", d), ("lex", l), ("emb", e)):
            r = rank_of(ids, nid)
            C[f"{sys_key}_mrr"] += (1.0 / r) if r else 0.0
            C[f"{sys_key}_hit5"] += 1 if (r and r <= 5) else 0
        C["n"] += 1

    sn = S["n"] or 1
    cn = C["n"] or 1
    return {
        "repo": path.name,
        "backend": backend,
        "index_s": round(index_s, 1),
        "files": stats["file_count"], "symbols": stats["symbol_count"],
        "links": stats["link_count"], "symbols_with_edges": len(gold),
        "structural": {
            "probes": S["n"],
            "neighbor_recall": {"droste": round(S["droste_nr"]/sn, 4),
                                 "embedding": round(S["emb_nr"]/sn, 4),
                                 "lexical": round(S["lex_nr"]/sn, 4)},
            "ndcg": {"droste": round(S["droste_ndcg"]/sn, 4),
                     "embedding": round(S["emb_ndcg"]/sn, 4),
                     "lexical": round(S["lex_ndcg"]/sn, 4)},
            "graph_only_neighbors": S["graph_only"],
        },
        "concept": {
            "probes": C["n"],
            "mrr": {"droste": round(C["droste_mrr"]/cn, 4),
                    "embedding": round(C["emb_mrr"]/cn, 4),
                    "lexical": round(C["lex_mrr"]/cn, 4)},
            "hit@5": {"droste": round(C["droste_hit5"]/cn, 4),
                      "embedding": round(C["emb_hit5"]/cn, 4),
                      "lexical": round(C["lex_hit5"]/cn, 4)},
        },
    }


def _avg(vals: list[float]) -> float:
    return round(sum(vals) / len(vals), 4) if vals else 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repos", nargs="*", default=DEFAULT_REPOS)
    ap.add_argument("--sample", type=int, default=60)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--budget", type=int, default=6000)
    ap.add_argument("--max-files", type=int, default=400)
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    repos = [Path(r) for r in args.repos if Path(r).is_dir()]
    log("=" * 74)
    log("  COMPARATIVE RETRIEVAL EVAL  —  Droste vs lexical vs vector-RAG cores")
    log("=" * 74)
    log(f"  repos={len(repos)}  sample/front={args.sample}  budget={args.budget}  "
        f"max_files={args.max_files}")

    reports = []
    for repo in repos:
        log(f"\n  indexing {repo.name} (real fastembed) ...")
        try:
            rep = eval_repo(repo, args.sample, args.seed, args.budget, args.max_files)
        except Exception as exc:  # noqa: BLE001
            log(f"  SKIP {repo.name}: {type(exc).__name__}: {exc}")
            continue
        reports.append(rep)
        s, c = rep["structural"], rep["concept"]
        log(f"    files={rep['files']} symbols={rep['symbols']} links={rep['links']} "
            f"edged={rep['symbols_with_edges']} idx={rep['index_s']}s "
            f"(struct probes={s['probes']}, concept probes={c['probes']})")

    if not reports:
        log("\n  no repos evaluated.")
        return 1

    # aggregate (macro-average across repos)
    def col(front, metric, sysk):
        return _avg([r[front][metric][sysk] for r in reports])

    log("\n" + "=" * 74)
    log("  AGGREGATE (macro-avg over repos)        droste   vector   lexical")
    log("=" * 74)
    log("  STRUCTURAL front (query = symbol name, gold = causal neighbours)")
    log(f"    neighbor-recall                       {col('structural','neighbor_recall','droste'):.3f}    "
        f"{col('structural','neighbor_recall','embedding'):.3f}    {col('structural','neighbor_recall','lexical'):.3f}")
    log(f"    nDCG@k                                {col('structural','ndcg','droste'):.3f}    "
        f"{col('structural','ndcg','embedding'):.3f}    {col('structural','ndcg','lexical'):.3f}")
    tot_graph_only = sum(r["structural"]["graph_only_neighbors"] for r in reports)
    log(f"    causal nodes ONLY graph found         {tot_graph_only}  (missed by BOTH baselines)")
    log("  CONCEPT front (query = NL docstring, gold = the symbol)")
    log(f"    MRR                                   {col('concept','mrr','droste'):.3f}    "
        f"{col('concept','mrr','embedding'):.3f}    {col('concept','mrr','lexical'):.3f}")
    log(f"    hit@5                                 {col('concept','hit@5','droste'):.3f}    "
        f"{col('concept','hit@5','embedding'):.3f}    {col('concept','hit@5','lexical'):.3f}")
    log("=" * 74)

    out = {"config": vars(args), "repos": reports, "aggregate": {
        "structural_neighbor_recall": {s: col("structural", "neighbor_recall", s) for s in ("droste", "embedding", "lexical")},
        "structural_ndcg": {s: col("structural", "ndcg", s) for s in ("droste", "embedding", "lexical")},
        "concept_mrr": {s: col("concept", "mrr", s) for s in ("droste", "embedding", "lexical")},
        "concept_hit5": {s: col("concept", "hit@5", s) for s in ("droste", "embedding", "lexical")},
        "graph_only_total": tot_graph_only,
    }}
    if args.json:
        Path(args.json).write_text(json.dumps(out, indent=2), encoding="utf-8")
        log(f"  wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
