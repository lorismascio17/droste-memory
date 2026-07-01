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

from core.droste_cli import _configure_windows_utf8_output
from core.droste_engine import DrosteConceptEngine, DrosteNode
from core.droste_ingester import DrosteProjectIngester
from core.droste_mcp import droste_status_payload
from conftest import force_hash_backend


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


# ---------------------------------------------------------------------------
# 4) MCP ROOT ISOLATION  (active_root prevents multi-repo contamination)
# ---------------------------------------------------------------------------
def _rooted_node(root: Path, node_id: str, label: str, subpath: str = "src/shared.py") -> DrosteNode:
    return _mk_node(
        0,
        id=node_id,
        title="shared_entry",
        summary=f"{label} repository symbol",
        detail_content=f"def shared_entry():\n    return '{label}'",
        source_path=str(root / subpath),
        line_start=1,
        line_end=2,
    )


def test_active_root_persists_and_context_filters_to_last_indexed_root(tmp_path: Path, engine):
    root_a = tmp_path / "repo_a"
    root_b = tmp_path / "repo_b"
    engine.replace_indexed_nodes([_rooted_node(root_a, "a-node", "alpha")], root_a, reset=True)
    engine.replace_indexed_nodes([_rooted_node(root_b, "b-node", "bravo")], root_b, reset=False)

    assert engine.active_root() == engine.normalize_root(root_b)

    reloaded = DrosteConceptEngine(db_path=engine.db_path)
    force_hash_backend(reloaded)
    assert reloaded.active_root() == engine.normalize_root(root_b)

    ingester = DrosteProjectIngester(reloaded)
    default_context = ingester.get_context("shared_entry", budget=1200)
    explicit_context = ingester.get_context("shared_entry", budget=1200, root=root_a)

    assert default_context["root"] == engine.normalize_root(root_b)
    assert default_context["selected_nodes"][0]["node"]["id"] == "b-node"
    assert "bravo" in default_context["compiled_context"]
    assert "alpha" not in default_context["compiled_context"]

    assert explicit_context["root"] == engine.normalize_root(root_a)
    assert explicit_context["selected_nodes"][0]["node"]["id"] == "a-node"
    assert "alpha" in explicit_context["compiled_context"]
    assert "bravo" not in explicit_context["compiled_context"]


def test_multi_root_without_active_root_warns_instead_of_mixing_context(tmp_path: Path, engine):
    root_a = tmp_path / "repo_a"
    root_b = tmp_path / "repo_b"
    engine.replace_indexed_nodes([_rooted_node(root_a, "a-node", "alpha")], root_a, reset=True)
    engine.replace_indexed_nodes([_rooted_node(root_b, "b-node", "bravo")], root_b, reset=False)
    engine.set_active_root(None)

    ingester = DrosteProjectIngester(engine)
    context = ingester.get_context("shared_entry", budget=1200)
    status = droste_status_payload(engine)

    assert context["selected_count"] == 0
    assert context["warnings"]
    assert "multiple indexed roots" in context["compiled_context"]
    assert "alpha" not in context["compiled_context"]
    assert "bravo" not in context["compiled_context"]

    assert status["node_count"] == 0
    assert status["warnings"]


# ---------------------------------------------------------------------------
# 5) QUERY-AWARE RANKING  (runtime first unless the query asks for tests/docs)
# ---------------------------------------------------------------------------
def test_query_aware_ranking_prefers_runtime_until_query_mentions_tests(tmp_path: Path, engine):
    root = tmp_path / "ranked"
    runtime = _rooted_node(root, "runtime-node", "runtime", subpath="src/payment.py")
    test = _rooted_node(root, "test-node", "test", subpath="tests/payment_test.py")
    runtime.title = "payment_service"
    test.title = "payment_service"
    engine.replace_indexed_nodes([runtime, test], root, reset=True)

    ingester = DrosteProjectIngester(engine)
    normal = ingester.search_nodes("payment service", limit=2, semantic=False, root=root)
    test_query = ingester.search_nodes("payment service test", limit=2, semantic=False, root=root)

    assert [match["node"].id for match in normal] == ["runtime-node", "test-node"]
    assert normal[0]["source_rank"] > 0
    assert normal[1]["source_rank"] < 0

    assert {match["source_rank"] for match in test_query} == {0.0}
    normal_gap = normal[0]["blended"] - normal[1]["blended"]
    test_query_gap = abs(test_query[0]["blended"] - test_query[1]["blended"])
    assert test_query_gap < normal_gap


# ---------------------------------------------------------------------------
# 6) WINDOWS UTF-8 SAFETY  (cp1252 consoles must not crash the CLI)
# ---------------------------------------------------------------------------
def test_windows_utf8_output_guard_reconfigures_and_swallows_failures():
    class FakeStream:
        def __init__(self) -> None:
            self.calls: list[dict[str, str]] = []

        def reconfigure(self, **kwargs) -> None:
            self.calls.append(kwargs)

    stdout = FakeStream()
    stderr = FakeStream()
    _configure_windows_utf8_output("win32", stdout, stderr)

    assert stdout.calls == [{"encoding": "utf-8", "errors": "replace"}]
    assert stderr.calls == [{"encoding": "utf-8", "errors": "replace"}]

    class BrokenStream:
        def reconfigure(self, **kwargs) -> None:
            raise OSError("cp1252 console refused reconfigure")

    _configure_windows_utf8_output("win32", BrokenStream(), BrokenStream())

    non_windows = FakeStream()
    _configure_windows_utf8_output("linux", non_windows, non_windows)
    assert non_windows.calls == []


# ---------------------------------------------------------------------------
# 7) FOCUS WORMHOLE PINNING  (the moat must survive small budgets)
# ---------------------------------------------------------------------------
def _pin_project(tmp_path: Path) -> Path:
    """A focus symbol with one true caller, drowned in lexical decoys that
    shout the query tokens without any causal tie."""
    proj = tmp_path / "pinproj"
    proj.mkdir()
    (proj / "engine.py").write_text(
        'def fingerprint_shard(data):\n'
        '    """Compute the structural shard fingerprint."""\n'
        '    return hash(data)\n'
        '\n'
        '\n'
        'def save_everything(payload):\n'
        '    """Persist a payload."""\n'
        '    return fingerprint_shard(payload)\n',
        encoding="utf-8",
    )
    for i in range(6):
        (proj / f"shard_fingerprint_notes_{i}.py").write_text(
            f'def shard_fingerprint_report_{i}():\n'
            f'    """shard fingerprint dirty shard fingerprint tracking shard."""\n'
            f'    return {i}\n',
            encoding="utf-8",
        )
    return proj


def test_focus_causal_neighbours_survive_small_budget(ingester, tmp_path):
    """Regression: at budget 1500 the focus's direct caller must be selected
    ahead of lexical lookalikes. Before the pin fix, neighbor-recall at 1500
    measured 0.068 with a NEGATIVE graph lift (-0.18): the causal neighbours
    were crowded out by keyword-matching seeds."""
    proj = _pin_project(tmp_path)
    ingester.index_project(str(proj), reset=True, max_files=50, max_symbols=500)
    res = ingester.get_context("fingerprint_shard", budget=1500)
    titles = " | ".join(s["node"]["title"] for s in res["selected_nodes"])
    roles = {s.get("wormhole_role") for s in res["selected_nodes"]}
    assert "caller" in roles, f"focus caller crowded out of the pack: {titles}"
    assert any(
        "save_everything" in s["node"]["title"] for s in res["selected_nodes"]
    ), f"true caller missing: {titles}"


def test_critical_neighbour_never_vanishes(ingester, tmp_path):
    """A neighbour whose body carries critical keywords (jwt/verify/...) used
    to be dropped entirely when its full form did not fit the per-node cap
    (the critical branch returned None). It must land at least as a stub."""
    proj = tmp_path / "critproj"
    proj.mkdir()
    filler = "\n".join(
        f'    jwt_check_{i} = verify("jwt token secret {i}")' for i in range(40)
    )
    (proj / "svc.py").write_text(
        'def issue_token(claims):\n'
        '    """Mint a token."""\n'
        '    return sign(claims)\n'
        '\n'
        '\n'
        'def gatekeeper(request):\n'
        '    """Verify jwt before issuing."""\n'
        f'{filler}\n'
        '    return issue_token(request)\n',
        encoding="utf-8",
    )
    ingester.index_project(str(proj), reset=True, max_files=20, max_symbols=200)
    res = ingester.get_context("issue_token", budget=1500)
    titles = " | ".join(s["node"]["title"] for s in res["selected_nodes"])
    assert any(
        "gatekeeper" in s["node"]["title"] for s in res["selected_nodes"]
    ), f"critical caller vanished instead of degrading to a stub: {titles}"
