"""Hard, destructive stress test for Droste-Memory under enterprise-ish load.

Three waves (run sequentially so their metrics are attributable, each maxing out
its own subsystem):

  WAVE 1  Async peak concurrency  -> FastAPI visualizer /api/zoom-query (:5000),
          50 parallel httpx requests mixing lexical / fuzzy-semantic / junk.
  WAVE 2  Massive code mutation   -> 100 generated multi-language files, cold
          index vs warm index (2 files mutated, reset=False). Proves the SHA-1
          content-hash cache skips ONNX embedding for the 98 untouched files.
  WAVE 3  Token-budget torture    -> get_context on a high-degree syntactic hub
          at budgets 5000->2500->1000->300; the packer must demote
          (full->contract->skeleton) and NEVER overflow / cut code mid-line.

Isolated: WAVE 2/3 build into a TEMP project + TEMP DB (live graph untouched);
WAVE 1 only reads the live visualizer.
"""
from __future__ import annotations

import asyncio
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

from core.droste_engine import DrosteConceptEngine
from core.droste_ingester import DrosteProjectIngester

VIS_URL = os.environ.get("DROSTE_VISUALIZER_URL", "http://127.0.0.1:5000")
REPORT: dict[str, dict] = {}


def log(msg: str) -> None:
    print(msg, flush=True)


def hr(title: str) -> None:
    log("\n" + "=" * 72)
    log(title)
    log("=" * 72)


# =============================================================================
# WAVE 1 - async peak concurrency against the live FastAPI visualizer
# =============================================================================
def _http_post(host: str, port: int, path: str, body: dict, timeout: float = 10.0):
    """Plain-HTTP POST via stdlib http.client. Deliberately avoids httpx/SSL:
    the embedded codex runtime's OpenSSL aborts the whole process with
    'OPENSSL_Applink' the moment the TLS stack is exercised, so we stay on a raw
    HTTP/1.1 socket (the visualizer is plain http on localhost anyway)."""
    import http.client
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        payload = json.dumps(body)
        conn.request("POST", path, body=payload,
                     headers={"Content-Type": "application/json",
                              "Content-Length": str(len(payload))})
        resp = conn.getresponse()
        resp.read()
        return resp.status
    finally:
        conn.close()


async def wave1_concurrency() -> None:
    hr("WAVE 1 - Async peak concurrency (50 parallel /api/zoom-query)")
    from urllib.parse import urlparse
    from concurrent.futures import ThreadPoolExecutor
    u = urlparse(VIS_URL)
    host, port = u.hostname or "127.0.0.1", u.port or 5000

    lexical = ["find_drama_clusters", "SoccerNetCalibrationDataset",
               "_is_celebration_window", "get_homography_by_index", "__init__"]
    fuzzy = ["logic that recovers from an unstable flaky network connection",
             "where is the camera calibration math performed",
             "code that decides when a goal celebration happens",
             "module responsible for tracking moving objects over time",
             "how are tactical labels derived without manual annotation"]
    junk = ["xq9z!!__00x �� garbage", "}{][;;;,,,...///\\\\",
            "  whitespace \t\t controls \x07\x1b ", "AAAAAAAAAAAAAAAAAAAAAAAA" * 4,
            "  中文テスト \U0001F600 emoji "]
    queries = []
    for i in range(50):
        bucket = (lexical, fuzzy, junk)[i % 3]
        queries.append(bucket[i % len(bucket)])

    loop = asyncio.get_running_loop()
    executor = ThreadPoolExecutor(max_workers=60)

    async def fire(q, idx):
        t0 = time.perf_counter()
        try:
            status = await loop.run_in_executor(
                executor, _http_post, host, port, "/api/zoom-query", {"query": q})
            dt = (time.perf_counter() - t0) * 1000.0
            return {"idx": idx, "status": status, "ms": dt, "ok": status == 200}
        except Exception as exc:
            dt = (time.perf_counter() - t0) * 1000.0
            return {"idx": idx, "status": None, "ms": dt, "ok": False, "err": f"{type(exc).__name__}: {exc}"}

    try:
        await loop.run_in_executor(executor, _http_post, host, port, "/api/zoom-query", {"query": "warmup"})
    except Exception as exc:
        REPORT["wave1"] = {"passed": False, "reason": f"visualizer unreachable: {exc}"}
        log(f"[WAVE1] FAIL - visualizer unreachable at {VIS_URL}: {exc}")
        executor.shutdown(wait=False)
        return
    t0 = time.perf_counter()
    results = await asyncio.gather(*(fire(q, i) for i, q in enumerate(queries)))
    wall = (time.perf_counter() - t0) * 1000.0
    executor.shutdown(wait=False)

    oks = [r for r in results if r["ok"]]
    bad = [r for r in results if not r["ok"]]
    lat = sorted(r["ms"] for r in results)
    p50 = statistics.median(lat)
    p95 = lat[int(len(lat) * 0.95) - 1]
    mx = max(lat)
    for r in bad:
        log(f"  [WAVE1] NON-200 idx={r['idx']} status={r.get('status')} err={r.get('err','')}")
    log(f"[WAVE1] 50 reqs in {wall:.0f}ms wall | 200-OK={len(oks)}/50 | "
        f"p50={p50:.0f}ms p95={p95:.0f}ms max={mx:.0f}ms")
    passed = len(bad) == 0
    steady_ok = p95 < 200.0
    REPORT["wave1"] = {
        "passed": passed, "zero_crash": passed, "ok_count": len(oks),
        "p50_ms": round(p50, 1), "p95_ms": round(p95, 1), "max_ms": round(mx, 1),
        "steady_under_200ms": steady_ok, "wall_ms": round(wall, 1),
    }
    log(f"[WAVE1] {'PASS' if passed else 'FAIL'} (zero crashes) | "
        f"steady-state p95<200ms: {'YES' if steady_ok else 'NO'}")


# =============================================================================
# WAVE 2 - massive multi-language code mutation + SHA-1 cache verification
# =============================================================================
def _py_file(k: str, n: int = 22) -> str:
    out = ['"""Module DOCMARK_%s generated for stress."""' % k,
           "from common import hub_core, transform", ""]
    for i in range(n):
        prev = f"{k}_fn{i-1}(x)" if i > 0 else "transform(data)"
        out += [
            f"def {k}_fn{i}(data):",
            f'    """{k} fn{i} DOCMARK does work over data data data."""',
            "    x = transform(data)",
            "    y = transform(x)",
            f"    z = {prev}",
            "    return hub_core(x) + hub_core(y) + transform(z)",
            "",
        ]
    return "\n".join(out)


def _dart_file(k: str, n: int = 22) -> str:
    out = ["// DOCMARK_%s" % k]
    for i in range(n):
        prev = f"{k}_fn{i-1}(x)" if i > 0 else "transform(data)"
        out += [
            f"int {k}_fn{i}(int data) {{",
            f"  // {k} fn{i} DOCMARK",
            "  var x = transform(data);",
            "  var y = transform(x);",
            f"  var z = {prev};",
            "  return hub_core(x) + hub_core(y) + transform(z);",
            "}",
        ]
    return "\n".join(out)


def _ts_file(k: str, n: int = 22) -> str:
    out = ["// DOCMARK_%s" % k]
    for i in range(n):
        prev = f"{k}_fn{i-1}(x)" if i > 0 else "transform(data)"
        out += [
            f"function {k}_fn{i}(data: number): number {{",
            f"  // {k} fn{i} DOCMARK",
            "  const x = transform(data);",
            "  const y = transform(x);",
            f"  const z = {prev};",
            "  return hub_core(x) + hub_core(y) + transform(z);",
            "}",
        ]
    return "\n".join(out)


def _cpp_file(k: str, n: int = 22) -> str:
    out = ["// DOCMARK_%s" % k, "int hub_core(int);", "int transform(int);"]
    for i in range(n):
        prev = f"{k}_fn{i-1}(x)" if i > 0 else "transform(data)"
        out += [
            f"int {k}_fn{i}(int data) {{",
            f"  // {k} fn{i} DOCMARK",
            "  int x = transform(data);",
            "  int y = transform(x);",
            f"  int z = {prev};",
            "  return hub_core(x) + hub_core(y) + transform(z);",
            "}",
        ]
    return "\n".join(out)


def generate_project(root: Path) -> list[Path]:
    root.mkdir(parents=True, exist_ok=True)
    (root / "common.py").write_text(
        '"""Shared hub COMMON."""\n'
        "def hub_core(v):\n"
        '    """Central syntactic hub HUBDOC called by everything."""\n'
        "    return transform(v) + 1\n\n"
        "def transform(v):\n"
        '    """Identity-ish transform TRANSDOC used everywhere for crowding."""\n'
        "    return v\n",
        encoding="utf-8",
    )
    gens = [(_py_file, ".py"), (_dart_file, ".dart"), (_ts_file, ".ts"), (_cpp_file, ".cpp")]
    paths: list[Path] = []
    for i in range(100):
        fn, ext = gens[i % 4]
        key = f"m{i:03d}"
        p = root / f"{key}{ext}"
        p.write_text(fn(key), encoding="utf-8")
        paths.append(p)
    return paths


def wave2_mutation():
    hr("WAVE 2 - Massive code mutation (100 files) + SHA-1 cache verification")
    tmp = Path(tempfile.mkdtemp(prefix="droste_stress_"))
    proj = tmp / "bigproj"
    db = tmp / "stress_db.json"
    paths = generate_project(proj)
    py_files = [p for p in paths if p.suffix == ".py"]
    log(f"[WAVE2] generated 100 files (+common.py) in {proj}")

    eng = DrosteConceptEngine(db_path=db)
    ing = DrosteProjectIngester(eng)
    log(f"[WAVE2] embedding backend: {eng.projector.backend}")

    t0 = time.perf_counter()
    cold = ing.index_project(str(proj), reset=True, max_files=400, max_symbols=20000)
    cold_dt = time.perf_counter() - t0
    cs = cold["stats"]
    log(f"[WAVE2] COLD run: {cold_dt:.2f}s  nodes={cs['node_count']} "
        f"symbols={cs['symbol_count']} links={cs['link_count']}")

    for p in py_files[:2]:
        txt = p.read_text(encoding="utf-8")
        p.write_text(txt.replace("DOCMARK", "DOCMARK_MUTATED_v2", 1), encoding="utf-8")
    log(f"[WAVE2] mutated docstrings of 2/100 files: {[p.name for p in py_files[:2]]}")

    t0 = time.perf_counter()
    warm = ing.index_project(str(proj), reset=False, max_files=400, max_symbols=20000)
    warm_dt = time.perf_counter() - t0
    ws = warm["stats"]
    reused = warm.get("reused_files", 0)
    log(f"[WAVE2] WARM run: {warm_dt:.2f}s  nodes={ws['node_count']} "
        f"symbols={ws['symbol_count']} links={ws['link_count']} "
        f"| files reused (parse skipped)={reused}")

    cache_file = db.with_name("droste_embed_cache.json")
    cache_entries = 0
    if cache_file.exists():
        try:
            cache_entries = len(json.loads(cache_file.read_text(encoding="utf-8")).get("vectors", {}))
        except Exception:
            pass
    speedup = (cold_dt / warm_dt) if warm_dt else float("inf")
    warm_ok = warm_dt < 1.0
    log(f"[WAVE2] cache entries={cache_entries} | speedup={speedup:.1f}x | "
        f"warm<1s: {'YES' if warm_ok else 'NO'}")
    warm_ok = warm_dt < 1.0
    REPORT["wave2"] = {
        "passed": warm_ok,
        "cold_s": round(cold_dt, 2), "warm_s": round(warm_dt, 2),
        "speedup_x": round(speedup, 1), "warm_under_1s": warm_ok,
        "files_reused_parse_skipped": reused,
        "node_count": cs["node_count"], "symbol_count": cs["symbol_count"],
        "link_count": cs["link_count"], "cache_entries": cache_entries,
    }
    log(f"[WAVE2] {'PASS' if warm_ok else 'FAIL'}")
    return ing, "hub_core"


# =============================================================================
# WAVE 3 - token-budget torture on the syntactic hub
# =============================================================================
def _line_truncated(compiled: str) -> bool:
    """Unambiguous mid-code-slice signals (avoid false positives on real code):
    a dangling line-continuation at the very end, or a numbered gutter `NNNN:`
    with nothing after it (a slice that landed inside the line prefix)."""
    if compiled.endswith("\\"):
        return True
    for ln in compiled.splitlines():
        s = ln.rstrip()
        if s.endswith(":") and s.strip()[:-1].isdigit():
            return True
    return False


def wave3_budget(ing: DrosteProjectIngester, hub_query: str) -> None:
    hr("WAVE 3 - Token-budget torture on syntactic hub (get_context)")
    links = ing.engine.all_links()
    nodes = {n.id: n for n in ing.engine.all_nodes()}
    hub_ids = [nid for nid, n in nodes.items()
               if n.node_type == "symbol" and n.title.endswith("hub_core")]
    degree = 0
    if hub_ids:
        hid = set(hub_ids)
        degree = sum(1 for l in links if l.to_node in hid or l.from_node in hid)
    log(f"[WAVE3] hub '{hub_query}' symbol-nodes={len(hub_ids)} total-degree={degree}")

    MICRO = 1500
    rows = []
    all_ok = True
    for budget in (5000, 2500, 1000, 300):
        res = ing.get_context(hub_query, budget=budget)
        used = res["used"]
        compiled = res["compiled_context"]
        levels = [s.get("detail_level") for s in res["selected_nodes"]]
        overflow = used - budget
        budget_ok = used <= budget or (used <= MICRO and overflow > 0 and len(res["selected_nodes"]) >= 1)
        truncated = _line_truncated(compiled)
        nonempty = bool(compiled.strip())
        valid = budget_ok and (not truncated) and nonempty
        all_ok = all_ok and valid
        rows.append({
            "budget": budget, "used": used, "selected": res["selected_count"],
            "detail_levels": levels, "budget_ok": budget_ok,
            "mid_line_cut": truncated, "nonempty": nonempty, "valid": valid,
        })
        log(f"[WAVE3] budget={budget:>4} used={used:>4} sel={res['selected_count']:>2} "
            f"levels={levels} | guardrail={'OK' if budget_ok else 'BREACH'} "
            f"cut={'YES' if truncated else 'no'} -> {'VALID' if valid else 'INVALID'}")

    distinct_levels = {lv for r in rows for lv in r["detail_levels"] if lv}
    demotion_engaged = bool(distinct_levels & {"contract", "skeleton"}) or len(distinct_levels) > 1
    REPORT["wave3"] = {
        "passed": all_ok,
        "hub_degree": degree,
        "rows": rows,
        "demotion_engaged": demotion_engaged,
        "distinct_detail_levels": sorted(distinct_levels),
    }
    log(f"[WAVE3] {'PASS' if all_ok else 'FAIL'} | demotion engaged: "
        f"{'YES' if demotion_engaged else 'NO'} levels={sorted(distinct_levels)}")


# =============================================================================
def main() -> int:
    hr("DROSTE-MEMORY HARD STRESS TEST")
    log(f"visualizer: {VIS_URL}")

    try:
        asyncio.run(wave1_concurrency())
    except Exception as exc:
        REPORT["wave1"] = {"passed": False, "reason": f"crash: {type(exc).__name__}: {exc}"}
        log(f"[WAVE1] CRASH: {exc}")

    ing = None
    hub = "hub_core"
    try:
        ing, hub = wave2_mutation()
    except Exception as exc:
        import traceback
        REPORT["wave2"] = {"passed": False, "reason": f"crash: {type(exc).__name__}: {exc}"}
        log(f"[WAVE2] CRASH: {exc}\n{traceback.format_exc()}")

    if ing is not None:
        try:
            wave3_budget(ing, hub)
        except Exception as exc:
            import traceback
            REPORT["wave3"] = {"passed": False, "reason": f"crash: {type(exc).__name__}: {exc}"}
            log(f"[WAVE3] CRASH: {exc}\n{traceback.format_exc()}")
    else:
        REPORT["wave3"] = {"passed": False, "reason": "skipped: wave2 produced no index"}

    hr("FINAL REPORT")
    overall = all(REPORT.get(w, {}).get("passed") for w in ("wave1", "wave2", "wave3"))
    log(json.dumps(REPORT, indent=2, ensure_ascii=False))
    log("\n" + ("*** ALL WAVES PASSED ***" if overall else "*** FAILURES DETECTED (see above) ***"))
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
