"""STRESS TEST 5 - "Fault Tolerance & Anti-Corruzione".

Does Droste-Memory v0.6.7 survive a brutal kill (power-loss / Ctrl+C) during a
massive shard rewrite, and self-heal on reboot with ZERO historical data loss?

The real persistence facts this test pins down (verified against
core/droste_engine.py @ v0.6.7):

  * `_save` shards the graph one file-per-source-path under `.droste/nodes/`
    and writes each shard with `_atomic_write` (write `*.tmp` -> `os.replace`).
    The meta-file `droste_memory_db.json` is written LAST. => Under the engine's
    OWN save path a hard kill can leave a stale-but-valid `.json` + an orphan
    `.tmp`, but never a truncated `.json`. To actually exercise the load-side
    recovery we therefore CRASH *inside* `_save`, injecting a raw partial write
    on the 250th shard (bypassing the atomic guard) — the worst case the design
    is built to absorb.

  * On reboot, `_load_or_initialize` reassembles the graph from shards and wraps
    each `json.loads(shard)` in `except (OSError, json.JSONDecodeError): continue`.
    => A truncated shard is DETECTED by JSON-parse failure and SKIPPED, the
    engine does not crash. (The blake2b `_shard_fingerprint` is the structural
    dirty-oracle, not a stored checksum: its job is to force the re-parse below.)

  * The skipped shard's nodes are now absent from the live graph, so the
    ingester reuse precondition (droste_ingester.py: file_node_id + every symbol
    id must still be present) FAILS for that file => it is re-parsed from the
    on-disk source on the next `index_project`, rewriting a valid shard.

Flow: baseline cold index -> spawn child that crashes mid-`_save` at shard #250
-> assert meta timestamp preserved + victim shard is invalid JSON -> reboot
engine (must not crash, must skip victim) -> heal via reset=False re-index ->
assert node count back to baseline, victim shard valid again, 0 data loss.

Everything runs in an isolated TemporaryDirectory + temp DB; the live graph is
never touched. Embedding backend forced to the deterministic hash fallback.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from core.droste_engine import DrosteConceptEngine          # noqa: E402
from core.droste_ingester import DrosteProjectIngester      # noqa: E402

CRASH_AT_SHARD = 250        # kill the process right after mangling this shard
N_FILES = 600               # > 500 source files => > 500 dirty shards


def log(msg: str = "") -> None:
    print(msg, flush=True)


def _force_hash_backend(eng: DrosteConceptEngine) -> None:
    eng.projector._model_checked = True
    eng.projector._backend = "hash"
    eng.projector._fastembed = None
    eng.projector._model = None


# ---------------------------------------------------------------------------
# Synthetic project (>=500 files so >=500 shards exist to rewrite)
# ---------------------------------------------------------------------------
def _py(k: str) -> str:
    out = ['"""mod %s"""' % k, "from common import hub_core", ""]
    for i in range(6):
        out += [f"def {k}_fn{i}(d):", "    return hub_core(d) + 1", ""]
    return "\n".join(out)


def _ts(k: str) -> str:
    out = ["// %s" % k]
    for i in range(6):
        out += [f"function {k}_fn{i}(d: number): number {{", "  return d + 1;", "}"]
    return "\n".join(out)


def build_project(root: Path) -> int:
    root.mkdir(parents=True, exist_ok=True)
    (root / "common.py").write_text(
        '"""hub"""\ndef hub_core(v):\n    return v + 1\n', encoding="utf-8"
    )
    written = 0
    for a in range(20):
        d = root / f"pkg_{a:02d}"
        d.mkdir(parents=True, exist_ok=True)
        for b in range(N_FILES // 20):
            ext_py = (written % 2 == 0)
            key = f"u{a:02d}{b:02d}"
            gen, ext = (_py, ".py") if ext_py else (_ts, ".ts")
            (d / f"{key}{ext}").write_text(gen(key), encoding="utf-8")
            written += 1
    return written


# ---------------------------------------------------------------------------
# CHILD MODE: load the already-built DB, force every shard dirty, and crash
# brutally INSIDE _save right after corrupting the Nth shard. Meta is the LAST
# thing _save writes, so it is left untouched (old coherent timestamp survives).
# ---------------------------------------------------------------------------
def run_child(db_path: Path) -> None:
    eng = DrosteConceptEngine(db_path=db_path)
    _force_hash_backend(eng)
    shard_dir = eng._shard_dir()

    real_atomic = DrosteConceptEngine._atomic_write
    state = {"n": 0}

    def crashing_atomic_write(path, text):
        p = Path(path)
        is_shard = p.parent == shard_dir and p.suffix == ".json"
        if is_shard:
            state["n"] += 1
            if state["n"] == CRASH_AT_SHARD:
                # Brutal power-loss simulation: a RAW, non-atomic, HALF write
                # straight onto the live shard path -> truncated invalid JSON,
                # then an instant kill with no interpreter/OS cleanup.
                half = text[: max(8, len(text) // 2)]
                with open(p, "w", encoding="utf-8") as fh:
                    fh.write(half)
                    fh.flush()
                    os.fsync(fh.fileno())
                victim = shard_dir.parent.parent / "crash_victim.txt"
                victim.write_text(p.name, encoding="utf-8")
                sys.stderr.write(f"[child] CRASH after corrupting shard #{state['n']} {p.name}\n")
                sys.stderr.flush()
                os._exit(1)          # instant kill, no finally / no flush
        return real_atomic(path, text)

    # Force EVERY shard dirty so _save actually rewrites all >=500 of them.
    eng._shard_fp = {}
    eng._atomic_write = crashing_atomic_write  # shadow the staticmethod
    eng._save(eng._data)
    # unreachable: _save crashes at shard #250
    sys.stderr.write("[child] ERROR: reached end of _save without crashing\n")
    os._exit(2)


# ---------------------------------------------------------------------------
# CONTROLLER
# ---------------------------------------------------------------------------
def _count_invalid_shards(shard_dir: Path) -> list[Path]:
    bad = []
    for f in sorted(shard_dir.glob("*.json")):
        try:
            json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            bad.append(f)
    return bad


def main() -> int:
    workdir = Path(tempfile.mkdtemp(prefix="droste_fault_"))
    proj = workdir / "proj"
    db = workdir / "fault_db.json"
    report: dict = {}
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, ok, detail))
        log(f"   [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

    try:
        # --- PHASE 0: baseline coherent index --------------------------------
        log("[0] BASELINE  -> building project + clean cold index ...")
        nfiles = build_project(proj)
        eng = DrosteConceptEngine(db_path=db)
        _force_hash_backend(eng)
        ing = DrosteProjectIngester(eng)
        ing.index_project(str(proj), reset=True, max_files=5000,
                          max_symbols=200_000, max_symbols_per_lang=100_000)
        baseline_nodes = len(eng.all_nodes())
        baseline_links = len(eng.all_links())
        shard_dir = eng._shard_dir()
        n_shards = len(list(shard_dir.glob("*.json")))
        meta_mtime_before = db.stat().st_mtime_ns
        meta_bytes_before = db.read_bytes()
        log(f"    files={nfiles} baseline_nodes={baseline_nodes:,} "
            f"links={baseline_links:,} shards={n_shards}")
        del eng, ing   # release engine before the crashing child touches the DB

        # --- PHASE 1: brutal crash mid shard-rewrite -------------------------
        log(f"[1] CRASH     -> spawning child, os._exit(1) after shard #{CRASH_AT_SHARD} ...")
        proc = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--child-crash", str(db)],
            capture_output=True, text=True,
        )
        log(f"    child exit code = {proc.returncode} (expected 1 = killed mid-write)")
        if proc.stderr.strip():
            for line in proc.stderr.strip().splitlines()[-3:]:
                log(f"    child> {line}")

        # --- PHASE 2: isolate the mutilated shard ----------------------------
        log("[2] ISOLATE   -> inspecting .droste/nodes/ + meta timestamp ...")
        bad = _count_invalid_shards(shard_dir)
        meta_mtime_after = db.stat().st_mtime_ns
        check("child died mid-write (exit code 1)", proc.returncode == 1,
              f"code={proc.returncode}")
        check("exactly one shard is now invalid JSON", len(bad) == 1,
              f"invalid={[p.name for p in bad]}")
        check("meta droste_memory_db.json NOT updated (timestamp preserved)",
              meta_mtime_after == meta_mtime_before and db.read_bytes() == meta_bytes_before,
              "mtime+bytes identical")
        victim = bad[0] if bad else None
        if victim:
            raw = victim.read_text(encoding="utf-8")
            still_parses = True
            try:
                json.loads(raw)
            except json.JSONDecodeError:
                still_parses = False
            check("victim shard is syntactically truncated", not still_parses,
                  f"{victim.name} len={len(raw)}B")

        # --- PHASE 3: reboot + auto-heal -------------------------------------
        log("[3] REBOOT    -> reloading engine against the corrupted shard dir ...")
        crashed = False
        reload_nodes = -1
        try:
            eng2 = DrosteConceptEngine(db_path=db)
            _force_hash_backend(eng2)
            reload_nodes = len(eng2.all_nodes())
        except Exception as exc:  # noqa: BLE001
            crashed = True
            report["reboot_error"] = f"{type(exc).__name__}: {exc}"
        check("engine did NOT crash on corrupt shard", not crashed,
              report.get("reboot_error", "clean load"))
        lost = baseline_nodes - reload_nodes if reload_nodes >= 0 else None
        check("corrupt shard gracefully skipped (nodes missing, not fatal)",
              reload_nodes >= 0 and 0 < (lost or 0) < baseline_nodes,
              f"loaded={reload_nodes:,} of {baseline_nodes:,} (missing {lost})")

        log("[3b] HEAL     -> index_project(reset=False): re-parse victim from source ...")
        ing2 = DrosteProjectIngester(eng2)
        t0 = time.perf_counter()
        ing2.index_project(str(proj), reset=False, max_files=5000,
                           max_symbols=200_000, max_symbols_per_lang=100_000)
        heal_s = time.perf_counter() - t0
        healed_nodes = len(eng2.all_nodes())
        bad_after = _count_invalid_shards(shard_dir)
        check("all shards valid JSON again after heal", len(bad_after) == 0,
              f"invalid_after={len(bad_after)}")
        check("node count fully restored to baseline (0 data loss)",
              healed_nodes == baseline_nodes,
              f"{healed_nodes:,} == {baseline_nodes:,}")

        # --- PHASE 4: durability round-trip ----------------------------------
        log("[4] VERIFY    -> final reboot to confirm healed graph persists ...")
        eng3 = DrosteConceptEngine(db_path=db)
        _force_hash_backend(eng3)
        final_nodes = len(eng3.all_nodes())
        final_links = len(eng3.all_links())
        check("healed graph survives a clean reboot",
              final_nodes == baseline_nodes and final_links == baseline_links,
              f"nodes={final_nodes:,} links={final_links:,}")

        report.update({
            "baseline_nodes": baseline_nodes, "baseline_links": baseline_links,
            "shards": n_shards, "victim_shard": victim.name if victim else None,
            "nodes_after_reboot": reload_nodes, "nodes_lost_pre_heal": lost,
            "nodes_after_heal": healed_nodes, "heal_s": round(heal_s, 2),
            "final_nodes": final_nodes, "final_links": final_links,
        })
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    # ------------------------------- REPORT ---------------------------------
    passed = all(ok for _, ok, _ in checks)
    log("")
    log("=" * 66)
    log("  STRESS TEST 5 - FAULT TOLERANCE & ANTI-CORRUPTION  |  Droste v0.6.7")
    log("=" * 66)
    log(f"  Baseline graph     : {report.get('baseline_nodes', '?'):,} nodes / "
        f"{report.get('baseline_links', '?'):,} links across "
        f"{report.get('shards', '?')} shards")
    log(f"  Injected crash     : os._exit(1) after corrupting shard "
        f"#{CRASH_AT_SHARD} -> {report.get('victim_shard')}")
    log(f"  Reboot (corrupted) : loaded {report.get('nodes_after_reboot'):,} nodes "
        f"(skipped {report.get('nodes_lost_pre_heal')} from the dead shard, NO crash)")
    log(f"  Auto-heal          : re-parsed victim from source in "
        f"{report.get('heal_s')}s -> {report.get('nodes_after_heal'):,} nodes")
    log(f"  Final round-trip   : {report.get('final_nodes'):,} nodes / "
        f"{report.get('final_links'):,} links")
    log("-" * 66)
    for name, ok, detail in checks:
        log(f"   [{'PASS' if ok else 'FAIL'}] {name}")
    log("-" * 66)
    integrity = (report.get("final_nodes") == report.get("baseline_nodes")
                 and report.get("final_links") == report.get("baseline_links"))
    log(f"  RESULT             : {'ALL CHECKS PASSED' if passed else 'FAILURES DETECTED'}")
    log(f"  DATA INTEGRITY     : {'100% — zero historical data loss' if integrity else 'DEGRADED'}")
    log("=" * 66)
    return 0 if passed else 1


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--child-crash":
        run_child(Path(sys.argv[2]))
    else:
        sys.exit(main())
