"""Deterministic regression suite for Droste-Memory core invariants.

Three properties the product must never silently regress on:

  * Sharded round-trip  — save a graph, reload it from disk, get back the
    mathematically identical node/link set.
  * Dirty-oracle isolation — the blake2b shard fingerprint must ignore cosmetic
    churn (created_at / updated_at / embedding *values*) yet react to any change
    in the AST signature, docstring/body, span, or content hash.
  * Packer guardrail — get_context at microscopic budgets must never emit a
    mid-line code cut and never return empty.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.droste_engine import DrosteConceptEngine, DrosteNode


ROOT = "/virtual/root"


def _mk_node(i: int, **over) -> DrosteNode:
    """A node with every persisted field populated and all coords inside the
    [-1, 1] clamp range so from_dict round-trips them without drift."""
    base = dict(
        id=f"n{i}",
        title=f"symbol_{i}",
        summary=f"summary for symbol {i}",
        detail_content=f"def symbol_{i}(x):\n    return x + {i}",
        node_type="symbol",
        source_path=f"pkg/file_{i}.py",
        line_start=1 + i,
        line_end=10 + i,
        content_hash=f"hash{i}",
        x=0.1 * (i % 5),
        y=-0.1 * (i % 5),
        fixed_x=0.2,
        fixed_y=-0.2,
        zoom_threshold=5.0 + i,
        embedding=[0.1, -0.25, 0.5, 0.125],
    )
    base.update(over)
    return DrosteNode(**base)


# ---------------------------------------------------------------------------
# 1) SHARDED ROUND-TRIP
# ---------------------------------------------------------------------------
def test_sharded_round_trip_is_identical(tmp_path: Path):
    db = tmp_path / "rt.json"
    eng = DrosteConceptEngine(db_path=db)

    nodes = [_mk_node(i) for i in range(12)]
    # parent/children wiring on one node so children survive serialization.
    nodes[0].children = ["n1", "n2"]
    nodes[1].parent_id = "n0"
    nodes[2].parent_id = "n0"
    links = [{"from": f"n{i}", "to": f"n{i+1}", "type": "syntax_dependency"}
             for i in range(11)]

    eng.replace_indexed_nodes(nodes, index_root=ROOT, reset=True, links=links)

    before_nodes = sorted((n.to_dict() for n in eng.all_nodes()), key=lambda d: d["id"])
    before_links = sorted((l.to_dict() for l in eng.all_links()),
                          key=lambda d: (d["from"], d["to"]))

    # Cold reload from disk: a brand-new engine reassembles from the shards.
    eng2 = DrosteConceptEngine(db_path=db)
    after_nodes = sorted((n.to_dict() for n in eng2.all_nodes()), key=lambda d: d["id"])
    after_links = sorted((l.to_dict() for l in eng2.all_links()),
                         key=lambda d: (d["from"], d["to"]))

    assert len(after_nodes) == 12
    assert len(after_links) == 11
    assert after_nodes == before_nodes, "node set drifted across save/reload"
    assert after_links == before_links, "link set drifted across save/reload"


def test_round_trip_preserves_embeddings_exactly(tmp_path: Path):
    db = tmp_path / "emb.json"
    eng = DrosteConceptEngine(db_path=db)
    vec = [0.0, 1.0, -1.0, 0.333333, 0.142857, -0.875]
    eng.replace_indexed_nodes([_mk_node(0, embedding=vec)], index_root=ROOT, reset=True)
    eng2 = DrosteConceptEngine(db_path=db)
    reloaded = eng2.all_nodes()[0]
    assert reloaded.embedding == vec


# ---------------------------------------------------------------------------
# 2) DIRTY-ORACLE ISOLATION  (blake2b _shard_fingerprint)
# ---------------------------------------------------------------------------
@pytest.fixture
def fp_engine(tmp_path: Path) -> DrosteConceptEngine:
    return DrosteConceptEngine(db_path=tmp_path / "fp.json")


def _fp(eng: DrosteConceptEngine, node: DrosteNode) -> str:
    return eng._shard_fingerprint([node.to_dict()])


@pytest.mark.parametrize("field,value", [
    ("created_at", "2099-01-01T00:00:00Z"),
    ("updated_at", "2099-01-01T00:00:00Z"),
])
def test_fingerprint_ignores_cosmetic_timestamps(fp_engine, field, value):
    base = _mk_node(0)
    fp_base = _fp(fp_engine, base)
    mutated = _mk_node(0, **{field: value})
    assert _fp(fp_engine, mutated) == fp_base, f"{field} must not dirty the shard"


def test_fingerprint_ignores_embedding_values_same_length(fp_engine):
    base = _mk_node(0, embedding=[0.1, 0.2, 0.3, 0.4])
    drifted = _mk_node(0, embedding=[0.9, -0.9, 0.0, 0.5])  # same length, new values
    assert _fp(fp_engine, drifted) == _fp(fp_engine, base)


def test_fingerprint_reacts_to_embedding_length_change(fp_engine):
    empty = _mk_node(0, embedding=[])
    filled = _mk_node(0, embedding=[0.1, 0.2, 0.3, 0.4])
    assert _fp(fp_engine, filled) != _fp(fp_engine, empty), \
        "empty->filled embedding must dirty the shard (lazy-embed correctness)"


@pytest.mark.parametrize("field,value", [
    ("title", "symbol_0_renamed"),          # AST signature change
    ("detail_content", "def symbol_0(x, y):\n    return x + y"),  # body/sig change
    ("summary", "a completely different docstring"),
    ("line_start", 999),                    # span moved
    ("line_end", 1234),
    ("content_hash", "deadbeef"),
    ("node_type", "class"),
    ("parent_id", "different_parent"),
])
def test_fingerprint_reacts_to_structural_change(fp_engine, field, value):
    base = _mk_node(0)
    fp_base = _fp(fp_engine, base)
    mutated = _mk_node(0, **{field: value})
    assert _fp(fp_engine, mutated) != fp_base, f"{field} change must dirty the shard"


# ---------------------------------------------------------------------------
# 3) PACKER GUARDRAIL  (get_context never cuts mid-line / never empty)
# ---------------------------------------------------------------------------
def _line_truncated(compiled: str) -> bool:
    """Unambiguous mid-code-slice signals: a dangling line-continuation at the
    very end, or a numbered gutter `NNNN:` with nothing after it (a slice that
    landed inside the line prefix)."""
    if compiled.endswith("\\"):
        return True
    for ln in compiled.splitlines():
        s = ln.rstrip()
        if s.endswith(":") and s.strip()[:-1].isdigit():
            return True
    return False


@pytest.mark.parametrize("budget", [100, 200, 300, 500, 800, 1500])
def test_packer_never_cuts_mid_line_at_tiny_budgets(ingester, sample_project, budget):
    ingester.index_project(str(sample_project), reset=True,
                           max_files=50, max_symbols=2000)
    res = ingester.get_context("hub_core", budget=budget)
    compiled = res["compiled_context"]

    assert compiled.strip(), f"empty context at budget={budget}"
    assert not _line_truncated(compiled), f"mid-line cut at budget={budget}"

    # The engine floors any sub-500 budget to 500 (its first anti-truncation
    # guardrail). Allow the documented single-node micro-overflow: one whole,
    # uncut node may exceed a tiny budget but must stay within MICRO.
    MICRO = 1500
    effective = max(500, budget)
    used = res["used"]
    within = used <= effective or (used <= MICRO and res["selected_count"] >= 1)
    assert within, f"budget guardrail breached: used={used} budget={budget}"


def test_packer_demotes_detail_as_budget_shrinks(ingester, sample_project):
    """A shrinking budget must engage LOD-demotion (full -> contract/skeleton)
    or drop node count — never silently overflow to keep full bodies."""
    ingester.index_project(str(sample_project), reset=True,
                           max_files=50, max_symbols=2000)
    wide = ingester.get_context("hub_core", budget=6000)
    tight = ingester.get_context("hub_core", budget=600)

    wide_levels = {s.get("detail_level") for s in wide["selected_nodes"]}
    tight_levels = {s.get("detail_level") for s in tight["selected_nodes"]}
    demoted = (
        bool(tight_levels & {"contract", "skeleton"})
        or tight["selected_count"] < wide["selected_count"]
        or tight_levels != wide_levels
    )
    assert demoted, f"no demotion: wide={wide_levels} tight={tight_levels}"
    assert tight["used"] <= max(500, 600) or tight["selected_count"] >= 1
