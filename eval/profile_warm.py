"""Profile the WARM index path to find what dominates after parse-skip."""
import cProfile, pstats, io, tempfile, time
from pathlib import Path
import eval.hard_stress_test as st
from core.droste_engine import DrosteConceptEngine
from core.droste_ingester import DrosteProjectIngester

tmp = Path(tempfile.mkdtemp(prefix="droste_prof_"))
proj = tmp / "bigproj"
db = tmp / "db.json"
paths = st.generate_project(proj)
py_files = [p for p in paths if p.suffix == ".py"]

eng = DrosteConceptEngine(db_path=db)
ing = DrosteProjectIngester(eng)
ing.index_project(str(proj), reset=True, max_files=400, max_symbols=20000)
for p in py_files[:2]:
    p.write_text(p.read_text(encoding="utf-8").replace("DOCMARK", "MUT", 1), encoding="utf-8")

pr = cProfile.Profile()
t = time.perf_counter()
pr.enable()
ing.index_project(str(proj), reset=False, max_files=400, max_symbols=20000)
pr.disable()
dt = time.perf_counter() - t
print(f"WARM total: {dt:.2f}s\n")
s = io.StringIO()
pstats.Stats(pr, stream=s).sort_stats("cumulative").print_stats(22)
# keep only the meaningful lines
for line in s.getvalue().splitlines():
    if any(k in line for k in ("droste_", "embedding_projector", "json", "{method", "cumtime", "ncalls", "replace_indexed", "_save", "build_dependency", "populate_embed", "all_nodes", "_load_", "to_dict", "from_dict")):
        print(line)
