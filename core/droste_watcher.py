"""Reactive elastic-space watcher for Droste-Memory 0.4.0+reactive.

A zero-dependency polling watcher (stdlib threading + os.scandir mtime
snapshots) that surgically re-ingests individual files into the live in-RAM
graph as they are created or modified in the indexed project roots.

Design notes:
- watchdog is NOT bundled; this uses polling instead. Latency is therefore
  ~poll_interval (default 1.0s), not "a millisecond". This is the honest
  zero-dependency trade-off.
- It MUST share the same engine/ingester instance that serves get_context
  (the FastAPI singleton), otherwise queries would never see the updates.
- The ingest + atomic save run on THIS background thread, off the request path.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from .droste_ingester import SKIP_DIRS, TEXT_EXTENSIONS, DrosteProjectIngester

WATCH_EXTENSIONS = {".py"}  # reactive scope: Python only (AST contracts/edges)
TEMP_SUFFIXES = {".tmp", ".swp", ".swx", ".part", ".crdownload"}
TEMP_NAME_SUFFIXES = ("~", ".bak")


class ReactiveWatcher:
    def __init__(
        self,
        ingester: DrosteProjectIngester,
        roots: list[str] | None = None,
        poll_interval: float = 1.0,
        debounce: float = 0.15,
    ) -> None:
        self.ingester = ingester
        self.engine = ingester.engine
        self._explicit_roots = roots
        self.poll_interval = max(0.2, float(poll_interval))
        self.debounce = max(0.0, float(debounce))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._mtimes: dict[str, float] = {}
        self._pending: dict[str, float] = {}
        self.last_event: dict | None = None

    def discover_roots(self) -> list[str]:
        if self._explicit_roots:
            return self._explicit_roots
        roots: set[str] = set()
        for node in self.engine.all_nodes():
            if node.node_type == "project" and node.index_root:
                roots.add(str(Path(node.index_root).resolve()))
        return sorted(roots)

    def _iter_files(self, root: str):
        for directory, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                d for d in dirnames
                if d not in SKIP_DIRS and not d.startswith(".cache")
            ]
            for filename in filenames:
                if self._watchable_name(filename):
                    yield os.path.join(directory, filename)

    @staticmethod
    def _watchable_name(filename: str) -> bool:
        name = filename.lower()
        if name.endswith(TEMP_NAME_SUFFIXES):
            return False
        suffix = Path(name).suffix
        if suffix in TEMP_SUFFIXES:
            return False
        return suffix in WATCH_EXTENSIONS

    def _snapshot(self, roots: list[str]) -> dict[str, float]:
        snap: dict[str, float] = {}
        for root in roots:
            if not os.path.isdir(root):
                continue
            for path in self._iter_files(root):
                try:
                    snap[path] = os.path.getmtime(path)
                except OSError:
                    continue
        return snap

    def _queue_changes(self, changed: list[str]) -> None:
        now = time.monotonic()
        for path in changed:
            self._pending[path] = now

    def _ready_changes(self) -> list[str]:
        if not self._pending:
            return []
        now = time.monotonic()
        ready = [
            path for path, queued_at in self._pending.items()
            if now - queued_at >= self.debounce
        ]
        for path in ready:
            self._pending.pop(path, None)
        return sorted(ready)

    def _loop(self) -> None:
        # Prime the baseline without re-ingesting everything already indexed.
        self._mtimes = self._snapshot(self.discover_roots())
        while not self._stop.wait(self.poll_interval):
            try:
                roots = self.discover_roots()
                current = self._snapshot(roots)
                changed = [
                    path for path, mtime in current.items()
                    if self._mtimes.get(path) != mtime
                ]
                if changed:
                    self._queue_changes(changed)
                for path in self._ready_changes():
                    if self._stop.is_set():
                        break
                    if path not in current:
                        continue
                    try:
                        result = self.ingester.ingest_file_incremental(path)
                        self.last_event = result
                    except Exception as exc:  # never let one bad file kill the watcher
                        self.last_event = {"status": "error", "path": path, "error": repr(exc)}
                self._mtimes = current
            except Exception as exc:  # defensive: keep the loop alive
                self.last_event = {"status": "loop_error", "error": repr(exc)}

    def start(self) -> "ReactiveWatcher":
        if self._thread and self._thread.is_alive():
            return self
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="droste-reactive-watcher", daemon=True
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.poll_interval + self.debounce + 1.0)
