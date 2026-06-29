"""Cross-process shard race — "La Guerra dei Processi".

A WRITER thread drives continuous `replace_indexed_nodes` commits (the
local-core re-index path) while two READER threads independently `glob` + read
`.droste/nodes/*.json` straight off disk — exactly what the live visualizer /
MCP process does, with NO shared lock with the writer.

The race: the engine writes shards first and the meta-file
`droste_memory_db.json` LAST (meta = the commit point, holding all links). A
careless reader that reads shards, then reads meta a moment later, can capture
meta from commit V+1 (a link to a brand-new node) against a shard snapshot from
commit V that does not contain that node yet -> a link dangling to a missing
node = a torn, inconsistent view.

The fix under test is an optimistic meta-stamp seqlock: read meta (version
token) -> read shards -> re-read meta; accept the snapshot only if the meta
token is byte-identical across the shard read. Because meta is written last and
the writer only ever GROWS the graph here (stable ids, monotonic add), a stable
meta token proves every link it carries resolves into the shards already on
disk. The guarded reader must therefore NEVER observe an inconsistency.

We assert the safety guarantee (guarded reader = 0 torn views) and merely
REPORT the naive reader's torn-view count (timing-dependent, so not asserted —
but it shows the race is real and that the seqlock is what closes it).
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from core.droste_engine import DrosteConceptEngine, DrosteNode
from conftest import force_hash_backend

BASE_NODES = 50
COMMITS = 40
NAIVE_GAP_S = 0.003          # widen the naive torn-view window so it's visible
READ_RETRY = 6
SNAPSHOT_RETRY = 200


def _mk_node(i: int, root: str) -> DrosteNode:
    return DrosteNode(
        id=f"n{i}",
        title=f"sym_{i}",
        summary=f"node {i}",
        detail_content=f"def sym_{i}(x):\n    return x + {i}",
        node_type="symbol",
        source_path=f"file_{i}.py",     # distinct path -> distinct shard
        index_root=root,
    )


def _safe_read_bytes(path: Path) -> bytes | None:
    """Tolerate transient Windows share violations during the writer's atomic
    tmp->replace by retrying briefly; None means 'could not read this time'."""
    for _ in range(READ_RETRY):
        try:
            return path.read_bytes()
        except OSError:
            time.sleep(0.0005)
    return None


def _read_shard_ids(shard_dir: Path, strict: bool) -> set[str] | None:
    """Union of node ids across all shards. strict=True returns None if ANY
    shard is momentarily unreadable (so the guarded reader retries the whole
    snapshot instead of under-counting and faking a dangling link)."""
    ids: set[str] = set()
    for f in shard_dir.glob("*.json"):
        raw = _safe_read_bytes(f)
        if raw is None:
            if strict:
                return None
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            if strict:
                return None
            continue
        if isinstance(payload, list):
            ids.update(item["id"] for item in payload if isinstance(item, dict) and "id" in item)
    return ids


def _dangling_count(meta: dict, shard_ids: set[str]) -> int:
    universe = shard_ids | {n["id"] for n in meta.get("nodes", []) if "id" in n}
    bad = 0
    for link in meta.get("links", []):
        if link.get("from") not in universe or link.get("to") not in universe:
            bad += 1
    return bad


@pytest.mark.slow
def test_cross_process_shard_race_is_tear_free_under_seqlock(tmp_path: Path, capsys):
    db = tmp_path / "war.json"
    root = str(tmp_path / "warroot")
    eng = DrosteConceptEngine(db_path=db)
    force_hash_backend(eng)
    shard_dir = eng._shard_dir()

    # Seed so the readers have something to read from the first tick.
    eng.replace_indexed_nodes(
        [_mk_node(i, root) for i in range(BASE_NODES)],
        index_root=root, reset=True,
        links=[{"from": f"n{i}", "to": f"n{i+1}", "type": "syntax_dependency"}
               for i in range(BASE_NODES - 1)],
    )

    stop = threading.Event()
    stats = {"writer_errors": 0,
             "naive_reads": 0, "naive_torn": 0,
             "guarded_reads": 0, "guarded_torn": 0, "guarded_retries": 0}

    # ---- WRITER: monotonic grow, one new node+link per commit ---------------
    def writer():
        for c in range(COMMITS):
            k = BASE_NODES + c + 1
            nodes = [_mk_node(i, root) for i in range(k)]
            links = [{"from": f"n{i}", "to": f"n{i+1}", "type": "syntax_dependency"}
                     for i in range(k - 1)]
            for _ in range(8):  # retry past transient Windows share violations
                try:
                    eng.replace_indexed_nodes(nodes, index_root=root, reset=True, links=links)
                    break
                except OSError:
                    time.sleep(0.002)
            else:
                stats["writer_errors"] += 1
            time.sleep(0.003)
        # Grace window: stop committing but keep the readers alive a beat so the
        # seqlock reader gets quiet windows to accumulate stable snapshots (it is
        # starved during the hot war) — strengthens the 0-torn safety evidence.
        time.sleep(0.10)
        stop.set()

    # ---- NAIVE reader: shards, gap, then meta (susceptible to tearing) ------
    def naive_reader():
        while not stop.is_set():
            ids = _read_shard_ids(shard_dir, strict=False)
            time.sleep(NAIVE_GAP_S)
            raw = _safe_read_bytes(db)
            if raw is None or ids is None:
                continue
            try:
                meta = json.loads(raw)
            except json.JSONDecodeError:
                continue
            stats["naive_reads"] += 1
            if _dangling_count(meta, ids) > 0:
                stats["naive_torn"] += 1

    # ---- GUARDED reader: meta-stamp seqlock with retry ----------------------
    def guarded_reader():
        while not stop.is_set():
            snap = None
            for attempt in range(SNAPSHOT_RETRY):
                b0 = _safe_read_bytes(db)
                if b0 is None:
                    continue
                ids = _read_shard_ids(shard_dir, strict=True)
                if ids is None:
                    stats["guarded_retries"] += 1
                    continue
                b1 = _safe_read_bytes(db)
                if b1 is None:
                    continue
                if b0 == b1:                       # meta stable across shard read
                    snap = (json.loads(b0), ids)
                    break
                stats["guarded_retries"] += 1      # a commit landed: retry clean
            if snap is None:
                continue
            meta, ids = snap
            stats["guarded_reads"] += 1
            if _dangling_count(meta, ids) > 0:
                stats["guarded_torn"] += 1

    threads = [threading.Thread(target=naive_reader),
               threading.Thread(target=guarded_reader),
               threading.Thread(target=writer)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    # Final consistency: after the war, a clean reload must be coherent.
    eng_final = DrosteConceptEngine(db_path=db)
    force_hash_backend(eng_final)
    final_ids = {n.id for n in eng_final.all_nodes()}
    final_dangling = sum(
        1 for l in eng_final.all_links()
        if l.from_node not in final_ids or l.to_node not in final_ids
    )

    with capsys.disabled():
        print(
            "\n  [shard-war] "
            f"writer_errors={stats['writer_errors']} | "
            f"NAIVE reads={stats['naive_reads']} torn={stats['naive_torn']} | "
            f"GUARDED reads={stats['guarded_reads']} torn={stats['guarded_torn']} "
            f"retries={stats['guarded_retries']} | final_dangling={final_dangling}"
        )

    # SAFETY GUARANTEE: the seqlock reader never sees a torn view.
    assert stats["guarded_torn"] == 0, "seqlock reader observed an inconsistent shard set"
    assert stats["guarded_reads"] > 0, "guarded reader never obtained a stable snapshot"
    assert stats["writer_errors"] == 0, "writer failed to commit under read contention"
    # The committed graph is internally consistent once the dust settles.
    assert final_dangling == 0, "final persisted graph has dangling links"
    # (naive_torn is reported, not asserted: it is timing-dependent, but a
    #  non-zero value is the evidence that the race is real and the seqlock is
    #  what eliminates it.)
