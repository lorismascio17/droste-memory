"""Smoke: batch embedding + content-hash cache. Index core/ twice into a temp
DB; second pass must be all cache hits (≈0 new embeddings) and much faster, with
identical vectors."""
import time
import tempfile
from pathlib import Path

from core.droste_engine import DrosteConceptEngine
from core.droste_ingester import DrosteProjectIngester

tmp = Path(tempfile.mkdtemp())
db = tmp / "db.json"
target = str(Path("core").resolve())


def run():
    eng = DrosteConceptEngine(db_path=db)
    ing = DrosteProjectIngester(eng)
    t0 = time.perf_counter()
    res = ing.index_project(target, reset=True)
    dt = time.perf_counter() - t0
    # snapshot embeddings by node id
    emb = {n.id: tuple(n.embedding or ()) for n in eng.all_nodes() if n.embedding}
    return dt, res["stats"], emb


print(f"backend: {DrosteConceptEngine(db_path=db).projector.backend}")
cache = tmp / "droste_embed_cache.json"
if cache.exists():
    cache.unlink()

dt1, stats1, emb1 = run()
cache_size_1 = len(cache.read_text(encoding="utf-8")) if cache.exists() else 0
dt2, stats2, emb2 = run()

print(f"\npass1: {dt1:.2f}s  symbols={stats1['symbol_count']} nodes={stats1['node_count']}")
print(f"pass2: {dt2:.2f}s  (cache warm)")
print(f"cache file present: {cache.exists()}  bytes={cache_size_1}")
print(f"speedup pass2/pass1: {dt1/dt2:.1f}x" if dt2 else "n/a")

# vectors must be identical across passes (cache returns the same vector)
shared = set(emb1) & set(emb2)
identical = sum(1 for k in shared if emb1[k] == emb2[k])
print(f"embeddings identical across passes: {identical}/{len(shared)}")
ok = cache.exists() and identical == len(shared) and len(shared) > 0
print("\n=== PASS ===" if ok else "\n=== FAIL ===")
