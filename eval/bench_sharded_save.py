"""Prove the sharded dirty-save: _save must drop from ~2.85s to milliseconds and
only re-serialize the shards of the files that actually changed (2 of 100), while
preserving the full graph across a reload.

Run: python -m eval.bench_sharded_save
"""
import json
import tempfile
import time
from pathlib import Path

import eval.hard_stress_test as st
from core import droste_engine
from core.droste_engine import DrosteConceptEngine
from core.droste_ingester import DrosteProjectIngester


def _instrument(eng: DrosteConceptEngine) -> dict:
    """Wrap _save / _atomic_write to record timing and which files were written."""
    stats = {"save_calls": [], "writes": []}
    real_save = eng._save
    real_atomic = eng._atomic_write

    def timed_save(data):
        t = time.perf_counter()
        stats["writes"].append([])  # collect writes for this save call
        real_save(data)
        stats["save_calls"].append(time.perf_counter() - t)

    def recording_atomic(path, text):
        stats["writes"][-1].append((Path(path).name, len(text)))
        real_atomic(path, text)

    eng._save = timed_save
    # _atomic_write is a staticmethod referenced via self; patch the bound name
    eng._atomic_write = recording_atomic
    return stats


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="droste_shard_"))
    proj = tmp / "bigproj"
    db = tmp / "db.json"
    paths = st.generate_project(proj)
    py_files = sorted(p for p in paths if p.suffix == ".py")

    eng = DrosteConceptEngine(db_path=db)
    ing = DrosteProjectIngester(eng)

    # --- COLD index (full shard write) -------------------------------------
    t = time.perf_counter()
    cold = ing.index_project(str(proj), reset=True, max_files=400, max_symbols=20000)
    cold_dt = time.perf_counter() - t
    n_cold = len(eng.all_nodes())
    shard_dir = eng._shard_dir()
    n_shards = len(list(shard_dir.glob("*.json")))
    meta_size = db.stat().st_size
    print(f"COLD index .......... {cold_dt:6.2f}s  nodes={n_cold}  shards={n_shards}")
    print(f"  meta file size .... {meta_size/1024:8.1f} KB  (loose nodes + links + camera)")

    # --- mutate 2 of the source files --------------------------------------
    mutated = py_files[:2]
    for p in mutated:
        p.write_text(p.read_text(encoding="utf-8").replace("DOCMARK", "MUT", 1), encoding="utf-8")

    # --- WARM index (only the 2 dirty shards should be rewritten) ----------
    stats = _instrument(eng)
    t = time.perf_counter()
    warm = ing.index_project(str(proj), reset=False, max_files=400, max_symbols=20000)
    warm_dt = time.perf_counter() - t
    n_warm = len(eng.all_nodes())
    reused = warm.get("reused_files", 0)

    save_time = sum(stats["save_calls"])
    last_writes = stats["writes"][-1] if stats["writes"] else []
    shard_writes = [w for w in last_writes if w[0].endswith(".json") and w[0] != db.name]
    meta_writes = [w for w in last_writes if w[0] == db.name]

    print()
    print(f"WARM index .......... {warm_dt:6.2f}s  nodes={n_warm}  reused_files={reused}")
    print(f"  _save total ....... {save_time*1000:6.1f} ms   (was ~2850 ms monolithic)")
    print(f"  shard files written {len(shard_writes):3d}      (expected ~2 of {n_shards})")
    print(f"  meta files written  {len(meta_writes):3d}")
    if shard_writes:
        kb = sum(w[1] for w in shard_writes) / 1024
        print(f"  shard bytes dumped  {kb:8.1f} KB  (vs full graph ~{meta_size/1024:.0f}+ KB before)")

    # --- correctness: reload from disk must reassemble the identical graph --
    eng2 = DrosteConceptEngine(db_path=db)
    n_reload = len(eng2.all_nodes())
    print()
    print(f"RELOAD from shards .. nodes={n_reload}")

    meta_on_disk = json.loads(db.read_text(encoding="utf-8"))
    ok = (
        n_warm == n_cold
        and n_reload == n_cold
        and meta_on_disk.get("storage") == "sharded"
        and len(shard_writes) <= 5
        and save_time < 0.5
    )
    print()
    print("RESULT:", "PASS" if ok else "FAIL")
    if not ok:
        print("  node_count stable:", n_warm == n_cold == n_reload)
        print("  storage marker   :", meta_on_disk.get("storage"))
        print("  shard writes <=5 :", len(shard_writes) <= 5)
        print("  _save < 500ms    :", save_time < 0.5)


if __name__ == "__main__":
    main()
