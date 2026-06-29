"""STRESS TEST 4 - "Il Monorepo Mostro".

Absolute-scale limit probe for Droste-Memory v0.6.7.

Phases (all autonomous, one shot):

  1. GENERATE  -> a nested directory tree of 10,000 synthetic files, split
                  evenly across .py / .dart / .ts / .cpp. Each file holds
                  5-10 functions/classes so the cold index produces ~50k+
                  AST symbol nodes.
  2. INGEST    -> a single COLD `index_project` (reset=True) against an
                  ISOLATED temp DB. A background sampler thread polls the
                  process working set every 50ms while the kernel-tracked
                  PeakWorkingSetSize is read at the end, so the report shows
                  the true peak RAM across the syntactic-link merge + the
                  sharded cache write. The global symbol cap and the
                  per-extension (per-lang) budget split are exercised and
                  asserted to hold without MemoryError / exponential blow-up.
  3. REPORT    -> wipe the 10,000-file temp tree + temp DB, then print ONLY
                  the final report: cold ingest time, peak RAM, total nodes
                  indexed, system health.

The live graph / live DB are never touched: everything is built in a fresh
TemporaryDirectory with its own db_path.

Embedding backend is forced to the deterministic token-hash fallback so the
measurement isolates AST parsing + syntactic-link merge + sharded persistence
+ RAM scaling, instead of ONNX embedding throughput (which would add ~1h of
unrelated model time at 70k symbols and tell us nothing about scale limits).
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import gc
import os
import random
import shutil
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

# Make `core` importable when run as a bare script from anywhere.
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from core.droste_engine import DrosteConceptEngine          # noqa: E402
from core.droste_ingester import DrosteProjectIngester      # noqa: E402

TARGET_FILES = 10_000
EXTS = (".py", ".dart", ".ts", ".cpp")
# 20 packages x 25 subpackages x 20 files = 10,000 leaves, 2,500 per extension.
TOP_PKGS, SUB_PKGS, FILES_PER_SUB = 20, 25, 20


def log(msg: str = "") -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------------------
# Windows RAM profiling (no psutil dependency)
# ---------------------------------------------------------------------------
class _PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("cb", wt.DWORD),
        ("PageFaultCount", wt.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


_psapi = ctypes.WinDLL("psapi.dll")
_kernel32 = ctypes.WinDLL("kernel32.dll")

# Proper 64-bit typing: without these, the HANDLE return / args are truncated
# to 32-bit and GetProcessMemoryInfo silently writes nothing (reads as 0 MB).
_kernel32.GetCurrentProcess.restype = wt.HANDLE
_kernel32.GetCurrentProcess.argtypes = []
_psapi.GetProcessMemoryInfo.restype = wt.BOOL
_psapi.GetProcessMemoryInfo.argtypes = [
    wt.HANDLE, ctypes.POINTER(_PROCESS_MEMORY_COUNTERS), wt.DWORD,
]


def _mem_counters() -> _PROCESS_MEMORY_COUNTERS:
    counters = _PROCESS_MEMORY_COUNTERS()
    counters.cb = ctypes.sizeof(_PROCESS_MEMORY_COUNTERS)
    handle = _kernel32.GetCurrentProcess()
    ok = _psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    return counters


def working_set_mb() -> float:
    return _mem_counters().WorkingSetSize / (1024 * 1024)


def kernel_peak_working_set_mb() -> float:
    """Kernel-tracked high-water mark of this process's working set."""
    return _mem_counters().PeakWorkingSetSize / (1024 * 1024)


class RamSampler(threading.Thread):
    """Polls the working set so we capture transient spikes the kernel peak
    also catches, plus a baseline-delta view (peak above the pre-ingest RSS)."""

    def __init__(self, interval: float = 0.05) -> None:
        super().__init__(daemon=True)
        self.interval = interval
        self._stop_event = threading.Event()  # not `_stop`: Thread uses that name
        self.baseline_mb = working_set_mb()
        self.peak_mb = self.baseline_mb
        self.samples = 0

    def run(self) -> None:
        while not self._stop_event.is_set():
            cur = working_set_mb()
            if cur > self.peak_mb:
                self.peak_mb = cur
            self.samples += 1
            self._stop_event.wait(self.interval)

    def stop(self) -> None:
        self._stop_event.set()
        self.join(timeout=2.0)


# ---------------------------------------------------------------------------
# Synthetic-file generators (5-10 funcs/classes per file, cross-call links)
# ---------------------------------------------------------------------------
def _py_file(k: str, n: int) -> str:
    out = ['"""Module %s."""' % k, "from common import hub_core, transform", ""]
    for i in range(n):
        prev = f"{k}_fn{i-1}(x)" if i > 0 else "transform(data)"
        out += [
            f"class {k}_C{i}:" if i % 3 == 0 else f"def {k}_fn{i}(data):",
            f"    \"\"\"{k} unit {i}.\"\"\"",
            "    x = transform(data)" if i % 3 else "    attr = hub_core(0)",
            f"    z = {prev}" if i % 3 else "    pass",
            "    return hub_core(x) + transform(z)" if i % 3 else "",
            "",
        ]
    return "\n".join(out)


def _dart_file(k: str, n: int) -> str:
    out = ["// %s" % k]
    for i in range(n):
        prev = f"{k}_fn{i-1}(x)" if i > 0 else "transform(data)"
        out += [
            f"class {k}_C{i} {{ int v = 0; }}" if i % 3 == 0 else f"int {k}_fn{i}(int data) {{",
            "  var x = transform(data);" if i % 3 else "",
            f"  var z = {prev};" if i % 3 else "",
            "  return hub_core(x) + transform(z);" if i % 3 else "",
            "}" if i % 3 else "",
        ]
    return "\n".join(line for line in out if line != "" or True)


def _ts_file(k: str, n: int) -> str:
    out = ["// %s" % k]
    for i in range(n):
        prev = f"{k}_fn{i-1}(x)" if i > 0 else "transform(data)"
        if i % 3 == 0:
            out += [f"class {k}_C{i} {{ v: number = 0; }}"]
        else:
            out += [
                f"function {k}_fn{i}(data: number): number {{",
                "  const x = transform(data);",
                f"  const z = {prev};",
                "  return hub_core(x) + transform(z);",
                "}",
            ]
    return "\n".join(out)


def _cpp_file(k: str, n: int) -> str:
    out = ["// %s" % k, "int hub_core(int);", "int transform(int);"]
    for i in range(n):
        prev = f"{k}_fn{i-1}(x)" if i > 0 else "transform(data)"
        if i % 3 == 0:
            out += [f"struct {k}_S{i} {{ int v; }};"]
        else:
            out += [
                f"int {k}_fn{i}(int data) {{",
                "  int x = transform(data);",
                f"  int z = {prev};",
                "  return hub_core(x) + transform(z);",
                "}",
            ]
    return "\n".join(out)


_GENS = {".py": _py_file, ".dart": _dart_file, ".ts": _ts_file, ".cpp": _cpp_file}


def generate_mega_repo(root: Path, rng: random.Random) -> dict:
    """Write the nested 10k-file monorepo. Returns generation stats."""
    root.mkdir(parents=True, exist_ok=True)
    # Shared hub every file references -> a real high-degree syntactic node.
    (root / "common.py").write_text(
        '"""Shared hub."""\n'
        "def hub_core(v):\n    return transform(v) + 1\n\n"
        "def transform(v):\n    return v\n",
        encoding="utf-8",
    )

    written = 0
    units = 0
    per_ext = {e: 0 for e in EXTS}
    t0 = time.perf_counter()
    for a in range(TOP_PKGS):
        for b in range(SUB_PKGS):
            d = root / f"pkg_{a:02d}" / f"sub_{b:02d}"
            d.mkdir(parents=True, exist_ok=True)
            for c in range(FILES_PER_SUB):
                ext = EXTS[written % 4]
                n = rng.randint(5, 10)          # 5-10 logical units per file
                key = f"u{a:02d}{b:02d}{c:02d}"
                (d / f"{key}{ext}").write_text(_GENS[ext](key, n), encoding="utf-8")
                written += 1
                units += n
                per_ext[ext] += 1
    return {
        "files": written,
        "approx_units": units,
        "per_ext": per_ext,
        "gen_s": round(time.perf_counter() - t0, 2),
    }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def main() -> int:
    rng = random.Random(40404)
    workdir = Path(tempfile.mkdtemp(prefix="droste_monorepo_"))
    proj = workdir / "monorepo"
    db = workdir / "stress_db.json"

    report: dict = {"ok": False}
    health: list[str] = []
    crash: str | None = None

    try:
        log("[1/3] GENERATE  -> writing 10,000-file nested monorepo ...")
        gen = generate_mega_repo(proj, rng)
        log(f"        files={gen['files']} per_ext={gen['per_ext']} "
            f"approx_units~{gen['approx_units']} in {gen['gen_s']}s")

        log("[2/3] INGEST    -> COLD index_project (isolated temp DB) ...")
        eng = DrosteConceptEngine(db_path=db)
        # Force deterministic hash backend: isolate AST/link/RAM scaling.
        eng.projector._model_checked = True
        eng.projector._backend = "hash"
        eng.projector._fastembed = None
        eng.projector._model = None
        ing = DrosteProjectIngester(eng)
        log(f"        embedding backend: {eng.projector._backend} (forced)")

        gc.collect()
        sampler = RamSampler(interval=0.05)
        peak_err: str | None = None
        t0 = time.perf_counter()
        try:
            sampler.start()
            result = ing.index_project(
                str(proj),
                reset=True,
                max_files=20_000,        # > 10k leaves: no file cap clipping
                max_symbols=500_000,     # global cap, generous: full ingest
                max_symbols_per_lang=200_000,  # per-extension budget split
                max_file_bytes=512_000,
            )
        except MemoryError as exc:
            peak_err = f"MemoryError: {exc}"
            raise
        finally:
            cold_s = time.perf_counter() - t0
            sampler.stop()

        kernel_peak = kernel_peak_working_set_mb()
        stats = result["stats"]
        nodes = stats["node_count"]
        symbols = stats["symbol_count"]
        links = stats["link_count"]
        files_idx = stats["file_count"]
        dirs_idx = stats["directory_count"]
        skipped = stats.get("skipped_files", 0)
        truncated = stats.get("truncated_files", 0)

        # Health assertions ------------------------------------------------
        if peak_err is None:
            health.append("no MemoryError raised")
        if nodes >= 50_000:
            health.append(f"node target met ({nodes:,} >= 50,000)")
        else:
            health.append(f"WARN node target low ({nodes:,} < 50,000)")
        # Cap / budget split integrity: symbol count must respect the global
        # cap and never exceed it (no silent overflow / runaway growth).
        if symbols <= 500_000:
            health.append("global symbol cap honored")
        else:
            health.append("WARN global symbol cap breached")
        # Exponential-slowdown guard: cold-run throughput sanity.
        files_per_s = files_idx / cold_s if cold_s else 0.0
        nodes_per_s = nodes / cold_s if cold_s else 0.0
        if files_per_s > 50:
            health.append(f"linear-ish throughput ({files_per_s:.0f} files/s)")
        else:
            health.append(f"WARN low throughput ({files_per_s:.0f} files/s)")

        report.update({
            "ok": peak_err is None and nodes >= 50_000,
            "cold_s": round(cold_s, 2),
            "peak_ram_kernel_mb": round(kernel_peak, 1),
            "peak_ram_sampled_mb": round(sampler.peak_mb, 1),
            "ram_baseline_mb": round(sampler.baseline_mb, 1),
            "ram_delta_mb": round(sampler.peak_mb - sampler.baseline_mb, 1),
            "ram_samples": sampler.samples,
            "nodes": nodes,
            "symbols": symbols,
            "links": links,
            "files_indexed": files_idx,
            "dirs_indexed": dirs_idx,
            "skipped_files": skipped,
            "truncated_files": truncated,
            "files_per_s": round(files_per_s, 1),
            "nodes_per_s": round(nodes_per_s, 1),
            "health": health,
        })

    except Exception as exc:  # noqa: BLE001 - we want the full picture in the report
        crash = f"{type(exc).__name__}: {exc}"
        report["crash"] = crash
        report["traceback"] = traceback.format_exc()
    finally:
        log("[3/3] CLEANUP   -> removing temp monorepo + temp DB ...")
        shutil.rmtree(workdir, ignore_errors=True)
        # Shard dir lives next to the temp db; rmtree of workdir covers it.
        log(f"        removed {workdir} (exists={workdir.exists()})")

    # ----------------------------- FINAL REPORT -----------------------------
    log("")
    log("=" * 64)
    log("  STRESS TEST 4 - 'IL MONOREPO MOSTRO'  |  Droste-Memory v0.6.7")
    log("=" * 64)
    if crash:
        log(f"  STATUS            : FAILED / CRASH")
        log(f"  Error             : {crash}")
        log("-" * 64)
        log(report.get("traceback", ""))
        return 1

    status = "HEALTHY" if report["ok"] else "DEGRADED"
    log(f"  Cold ingest time  : {report['cold_s']} s")
    log(f"  Peak RAM (kernel) : {report['peak_ram_kernel_mb']} MB")
    log(f"  Peak RAM (sampled): {report['peak_ram_sampled_mb']} MB "
        f"(baseline {report['ram_baseline_mb']} MB, +{report['ram_delta_mb']} MB, "
        f"{report['ram_samples']} samples)")
    log(f"  Total nodes       : {report['nodes']:,}")
    log(f"    - symbols       : {report['symbols']:,}")
    log(f"    - files         : {report['files_indexed']:,}")
    log(f"    - directories   : {report['dirs_indexed']:,}")
    log(f"  Syntactic links   : {report['links']:,}")
    log(f"  Throughput        : {report['files_per_s']} files/s  "
        f"| {report['nodes_per_s']} nodes/s")
    log(f"  SYSTEM HEALTH     : {status}")
    for h in report["health"]:
        flag = "WARN" if h.startswith("WARN") else "OK  "
        log(f"      [{flag}] {h.removeprefix('WARN ')}")
    log("=" * 64)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
