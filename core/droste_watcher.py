"""Batch refresh of supported files, including deletions and renames.

Each batch refreshes its root with cached extraction/embeddings and rebuilds
all links. Single-file splicing cannot safely preserve incoming references.
"""
from __future__ import annotations

from pathlib import Path
import threading
import time

from .droste_ingester import DrosteProjectIngester


class ReactiveWatcher:
    def __init__(self, ingester: DrosteProjectIngester, roots: list[str] | None = None,
                 poll_interval: float = 1.0, debounce: float = 0.3) -> None:
        self.ingester, self.engine = ingester, ingester.engine
        self._explicit_roots = roots
        self.poll_interval = max(0.2, float(poll_interval))
        self.debounce = max(0.0, float(debounce))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._observed: dict | None = None
        self._pending: dict[str, float] = {}
        self.last_event: dict | None = None

    def discover_roots(self) -> list[str]:
        return sorted({str(Path(r).resolve()) for r in self._explicit_roots}) if self._explicit_roots else self.engine.indexed_roots()

    def _snapshot(self) -> dict:
        snapshots = {}
        for root in self.discover_roots():
            state = self.engine.index_state(root) or {}
            maximum = state.get("config", {}).get("max_files", 600)
            files = self.ingester._collect_files(Path(root), maximum + 1)
            snapshot = {"__root_exists__": Path(root).is_dir()}
            for path in files:
                try:
                    stat = path.stat()
                    snapshot[str(path)] = (stat.st_mtime_ns, stat.st_size)
                except OSError:
                    continue
            snapshots[root] = snapshot
        return snapshots

    def poll_once(self) -> list[dict]:
        current = self._snapshot()
        if self._observed is None:
            self._observed = current
            return []
        now = time.monotonic()
        for root, snapshot in current.items():
            if snapshot != self._observed.get(root):
                self._pending[root] = now
        self._observed = current
        events = []
        for root in list(self._pending):
            if now - self._pending[root] < self.debounce:
                continue
            active_root = self.engine.active_root()
            try:
                state = self.engine.index_state(root) or {}
                result = self.ingester.index_project(root, **state.get("config", {}))
                if active_root and active_root != root:
                    self.engine.set_active_root(active_root)
                self.last_event = {"status": "refreshed", "root": root, "index": result}
                self._pending.pop(root, None)
            except Exception as exc:
                # Keep the batch for retry, and report the failure.
                self.last_event = {"status": "error", "root": root, "error": str(exc)}
            events.append(self.last_event)
        return events

    def _loop(self) -> None:
        while not self._stop.wait(self.poll_interval):
            try:
                self.poll_once()
            except Exception as exc:
                self.last_event = {"status": "loop_error", "error": str(exc)}

    def start(self) -> ReactiveWatcher:
        if self._thread and self._thread.is_alive():
            return self
        self._stop.clear()
        self.poll_once()
        self._thread = threading.Thread(target=self._loop, name="droste-watcher", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.poll_interval + 5)
