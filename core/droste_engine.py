"""Droste-Memory spatial engine.

This module owns the JSON database, camera math, FOV visibility rules, and
detail unlocking behavior used by both the MCP server and the web visualizer.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any

from .embedding_projector import EmbeddingProjector


DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "droste_memory_db.json"
LOCKED_DETAIL = "[Richiesto zoom ulteriore per svelare questo sottomondo]"
VISUALIZER_NODE_LIMIT = 1600
VISUALIZER_LINK_LIMIT = 320
VISUALIZER_LINK_ZOOM_THRESHOLD = 8.0
VISUALIZER_WORMHOLE_NEIGHBOR_LIMIT = 180
# Fragmented persistence: each indexed source file's nodes are stored in their own
# shard JSON under <db_parent>/.droste/nodes/, named sha1(source_path).json. A save
# only rewrites shards whose content actually changed (dirty-tracking), replacing
# the O(N) full-graph json.dumps that dominated warm re-index latency.
SHARD_SUBDIR = (".droste", "nodes")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def make_node_id(title: str, summary: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:42]
    slug = slug or "concept"
    digest = hashlib.sha1(f"{title}\n{summary}".encode("utf-8")).hexdigest()[:8]
    return f"{slug}-{digest}"


@dataclass
class DrosteNode:
    id: str
    title: str
    summary: str
    detail_content: str
    node_type: str = "concept"
    parent_id: str | None = None
    children: list[str] = field(default_factory=list)
    source_path: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    index_root: str | None = None
    content_hash: str | None = None
    x: float = 0.0
    y: float = 0.0
    semantic_x: float | None = None
    semantic_y: float | None = None
    fixed_x: float | None = None
    fixed_y: float | None = None
    zoom_threshold: float = 20.0
    embedding: list[float] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DrosteNode":
        title = str(data.get("title", "Untitled concept"))
        summary = str(data.get("summary", ""))
        return cls(
            id=str(data.get("id") or make_node_id(title, summary)),
            title=title,
            summary=summary,
            detail_content=str(data.get("detail_content", "")),
            node_type=str(data.get("node_type") or "concept"),
            parent_id=(str(data["parent_id"]) if data.get("parent_id") else None),
            children=[str(child) for child in data.get("children", [])],
            source_path=(str(data["source_path"]) if data.get("source_path") else None),
            line_start=(
                max(1, int(data["line_start"]))
                if data.get("line_start") is not None
                else None
            ),
            line_end=(
                max(1, int(data["line_end"]))
                if data.get("line_end") is not None
                else None
            ),
            index_root=(str(data["index_root"]) if data.get("index_root") else None),
            content_hash=(str(data["content_hash"]) if data.get("content_hash") else None),
            x=float(data.get("x", 0.0)),
            y=float(data.get("y", 0.0)),
            semantic_x=(
                clamp(float(data["semantic_x"]), -1.0, 1.0)
                if data.get("semantic_x") is not None
                else None
            ),
            semantic_y=(
                clamp(float(data["semantic_y"]), -1.0, 1.0)
                if data.get("semantic_y") is not None
                else None
            ),
            fixed_x=(
                clamp(float(data["fixed_x"]), -1.0, 1.0)
                if data.get("fixed_x") is not None
                else None
            ),
            fixed_y=(
                clamp(float(data["fixed_y"]), -1.0, 1.0)
                if data.get("fixed_y") is not None
                else None
            ),
            zoom_threshold=max(0.1, float(data.get("zoom_threshold", 20.0))),
            embedding=[float(value) for value in data.get("embedding", [])],
            created_at=str(data.get("created_at") or utc_now()),
            updated_at=str(data.get("updated_at") or utc_now()),
        )

    def to_dict(self) -> dict[str, Any]:
        # Hand-built instead of dataclasses.asdict(): asdict reflects over every
        # field AND deep-copies the 384-float embedding, which is then discarded
        # and recopied below — a double pass over the embedding for every node on
        # every save. The manual build does a single pass and skips the dataclass
        # reflection, roughly halving to_dict (the dominant warm-save cost once
        # the monolithic json.dumps is gone). Output is byte-for-byte equivalent.
        data: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "summary": self.summary,
            "detail_content": self.detail_content,
            "node_type": self.node_type,
            "x": float(self.x),
            "y": float(self.y),
            "zoom_threshold": float(self.zoom_threshold),
            # float() coercion keeps numpy-float embeddings JSON-serializable.
            "embedding": [float(value) for value in self.embedding],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if self.parent_id is not None:
            data["parent_id"] = self.parent_id
        if self.children:
            data["children"] = list(self.children)
        if self.source_path is not None:
            data["source_path"] = self.source_path
        if self.line_start is not None:
            data["line_start"] = self.line_start
        if self.line_end is not None:
            data["line_end"] = self.line_end
        if self.index_root is not None:
            data["index_root"] = self.index_root
        if self.content_hash is not None:
            data["content_hash"] = self.content_hash
        if self.semantic_x is not None:
            data["semantic_x"] = self.semantic_x
        if self.semantic_y is not None:
            data["semantic_y"] = self.semantic_y
        if self.fixed_x is not None:
            data["fixed_x"] = self.fixed_x
        if self.fixed_y is not None:
            data["fixed_y"] = self.fixed_y
        return data


@dataclass
class DrosteLink:
    from_node: str
    to_node: str
    type: str = "dependency"
    label: str | None = None
    index_root: str | None = None
    weight: float = 1.0
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DrosteLink":
        return cls(
            from_node=str(data.get("from") or data.get("from_node") or ""),
            to_node=str(data.get("to") or data.get("to_node") or ""),
            type=str(data.get("type") or "dependency"),
            label=(str(data["label"]) if data.get("label") else None),
            index_root=(str(data["index_root"]) if data.get("index_root") else None),
            weight=max(0.0, float(data.get("weight", 1.0))),
            created_at=str(data.get("created_at") or utc_now()),
            updated_at=str(data.get("updated_at") or utc_now()),
        )

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "from": self.from_node,
            "to": self.to_node,
            "type": self.type,
            "weight": float(self.weight),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if self.label:
            data["label"] = self.label
        if self.index_root:
            data["index_root"] = self.index_root
        return data


class DrosteConceptEngine:
    """Coordinate concepts, camera state, and detail visibility."""

    def __init__(
        self,
        db_path: str | Path = DEFAULT_DB_PATH,
        projector: EmbeddingProjector | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.projector = projector or EmbeddingProjector()
        self._lock = RLock()
        self._data = self._load_or_initialize()
        # Two-process staleness guard: remember the mtime we loaded so any read
        # can self-heal if another process (e.g. a local-core re-index) wrote the
        # db underneath us. Without this the visualizer served stale code until
        # restart — a context engine must never answer from a dead graph.
        self._loaded_mtime = self._db_mtime()
        # Parsed-object cache: rebuilding 1961 DrosteNode objects (each with a
        # 384-float embedding) from `_data` dicts on every read dominated
        # get_context latency. Cache the parse keyed on the identity of the
        # underlying list. Every mutation reassigns `_data["nodes"]`/["links"]
        # (and reloads reassign `_data` wholesale), so a changed id() means the
        # cache is stale -> rebuild. In-place list mutation never happens.
        self._nodes_cache: list[DrosteNode] | None = None
        self._nodes_cache_key: int | None = None
        self._links_cache: list[DrosteLink] | None = None
        self._links_cache_key: int | None = None

    def inject_concept(
        self,
        title: str,
        summary: str,
        detail_content: str,
        zoom_threshold: float = 20.0,
        x: float | None = None,
        y: float | None = None,
    ) -> dict[str, Any]:
        clean_title = (title or "").strip()
        if not clean_title:
            raise ValueError("title is required")

        clean_summary = (summary or "").strip()
        clean_detail = (detail_content or "").strip()
        threshold = max(0.1, float(zoom_threshold))
        if (x is None) != (y is None):
            raise ValueError("x and y must be provided together")
        fixed_x = clamp(float(x), -1.0, 1.0) if x is not None else None
        fixed_y = clamp(float(y), -1.0, 1.0) if y is not None else None

        with self._lock:
            nodes = self._nodes()
            embedding = self.projector.embed_text(f"{clean_title}\n{clean_summary}")
            node = DrosteNode(
                id=self._unique_node_id(make_node_id(clean_title, clean_summary), nodes),
                title=clean_title,
                summary=clean_summary,
                detail_content=clean_detail,
                x=fixed_x if fixed_x is not None else 0.0,
                y=fixed_y if fixed_y is not None else 0.0,
                semantic_x=fixed_x,
                semantic_y=fixed_y,
                fixed_x=fixed_x,
                fixed_y=fixed_y,
                zoom_threshold=threshold,
                embedding=embedding,
            )
            nodes.append(node)

            embeddings = [self._embedding_for_node(existing) for existing in nodes]
            projection = self.projector.project_embeddings(embeddings)

            for existing, (projected_x, projected_y) in zip(nodes, projection.coordinates):
                if existing.fixed_x is not None and existing.fixed_y is not None:
                    existing.x = existing.fixed_x
                    existing.y = existing.fixed_y
                    existing.semantic_x = existing.fixed_x
                    existing.semantic_y = existing.fixed_y
                else:
                    anchor_x = clamp(float(projected_x), -1.0, 1.0)
                    anchor_y = clamp(float(projected_y), -1.0, 1.0)
                    existing.x = anchor_x
                    existing.y = anchor_y
                    existing.semantic_x = anchor_x
                    existing.semantic_y = anchor_y
                existing.updated_at = utc_now()

            self._data["nodes"] = [existing.to_dict() for existing in nodes]
            self._touch_and_save()

            return {
                "status": "inserted",
                "coordinates": {"x": node.x, "y": node.y},
                "zoom_threshold": node.zoom_threshold,
                "node": self._public_node(node),
                "projection": {
                    "method": projection.method,
                    "warning": projection.warning,
                },
            }

    def move_camera_and_zoom(self, x: float, y: float, zoom_level: float) -> dict[str, Any]:
        with self._lock:
            camera = {
                "x": clamp(float(x), -1.0, 1.0),
                "y": clamp(float(y), -1.0, 1.0),
                "zoom": max(0.1, float(zoom_level)),
            }
            self._data["camera"] = camera
            # Camera is high-frequency UI state (fired on every wheel/pan tick).
            # Persisting it via _touch_and_save() rewrote the ENTIRE db on each
            # move — negligible when the graph was tiny, but ~800ms once the
            # semantic embeddings grew the db to ~25MB. That full-graph write per
            # frame is the zoom-lag regression. Keep camera in RAM only: it still
            # reflects live through get_visualizer_state, and rides along on the
            # next real mutation's save. Durable per-frame camera is not worth a
            # 25MB disk write per zoom tick.
            return {
                "camera": camera,
                "fov": self.calculate_fov(camera["zoom"]),
                "visible_nodes": self.visible_nodes(camera),
            }

    def reset_space(self) -> dict[str, Any]:
        with self._lock:
            self._data = self._empty_database()
            self._touch_and_save()
            return self.get_space_status()

    def replace_indexed_nodes(
        self,
        nodes: list[DrosteNode],
        index_root: str,
        reset: bool = False,
        links: list[dict[str, Any] | DrosteLink] | None = None,
    ) -> dict[str, Any]:
        clean_root = str(Path(index_root).resolve())
        with self._lock:
            current_nodes = self._nodes()
            current_links = self._links()
            if reset:
                existing_nodes: list[DrosteNode] = []
                existing_links: list[DrosteLink] = []
            else:
                existing_nodes = [
                    node for node in current_nodes
                    if node.index_root
                    and str(Path(node.index_root).resolve()) != clean_root
                ]
                existing_nodes.extend(
                    node for node in current_nodes
                    if not node.index_root
                )
                existing_links = [
                    link for link in current_links
                    if not link.index_root
                    or str(Path(link.index_root).resolve()) != clean_root
                ]

            for node in nodes:
                node.index_root = clean_root
                if node.semantic_x is None:
                    node.semantic_x = node.fixed_x if node.fixed_x is not None else node.x
                if node.semantic_y is None:
                    node.semantic_y = node.fixed_y if node.fixed_y is not None else node.y
                node.updated_at = utc_now()

            all_nodes = [*existing_nodes, *nodes]
            node_dicts = [node.to_dict() for node in all_nodes]
            self._data["nodes"] = node_dicts
            self._data["active_root"] = clean_root
            # Prime the parsed-node cache directly with the live objects we already
            # hold, so the get_space_status() below (and the next reads) need not
            # rebuild all nodes from their dicts via from_dict. The cache key is the
            # identity of the dict list we just assigned, so any later mutation that
            # reassigns _data["nodes"] still invalidates it correctly.
            self._nodes_cache = list(all_nodes)
            self._nodes_cache_key = id(node_dicts)
            if links is not None or reset:
                indexed_links = self._normalize_links(links or [], clean_root)
                self._data["links"] = [
                    link.to_dict() for link in [*existing_links, *indexed_links]
                ]
            self._touch_and_save()
            return self.get_space_status()

    def upsert_file(
        self,
        new_nodes: list[DrosteNode],
        new_links: list[dict[str, Any]],
        removed_node_ids: set[str],
        attachments: dict[str, str],
    ) -> dict[str, Any]:
        """Atomically splice one file's subtree into the live graph in RAM.

        Removes any prior nodes/links for the file (modify), inserts the new
        nodes, fixes parent.children both ways, and persists with the same
        atomic tmp+rename `_save`. Called off the request path (watcher thread).
        """
        with self._lock:
            node_dicts = [
                item for item in self._data.get("nodes", [])
                if item.get("id") not in removed_node_ids
            ]
            by_id: dict[str, dict[str, Any]] = {item["id"]: item for item in node_dicts}

            # purge removed ids from any surviving children lists
            for item in node_dicts:
                children = item.get("children")
                if children:
                    item["children"] = [c for c in children if c not in removed_node_ids]

            for node in new_nodes:
                by_id[node.id] = node.to_dict()

            for child_id, parent_id in attachments.items():
                parent = by_id.get(parent_id)
                if parent is None:
                    continue
                children = parent.setdefault("children", [])
                if child_id not in children:
                    children.append(child_id)

            self._data["nodes"] = list(by_id.values())

            # Drop only OUTGOING edges from removed nodes (they get recomputed
            # by the incremental re-ingest). Incoming edges from OTHER files
            # MUST survive: a re-ingested symbol keeps its stable id, so its
            # external callers would otherwise silently lose their edge. Edges
            # to a genuinely deleted symbol become dangling and are filtered
            # harmlessly at query time (and cleaned on the next full re-index).
            links = [
                link for link in self._data.get("links", [])
                if link.get("from") not in removed_node_ids
            ]
            seen = {
                (link.get("from"), link.get("to"), link.get("type"), link.get("label"))
                for link in links
            }
            for link in new_links:
                if link.get("from") == link.get("to"):
                    continue
                key = (link.get("from"), link.get("to"), link.get("type"), link.get("label"))
                if key in seen:
                    continue
                seen.add(key)
                links.append(link)
            self._data["links"] = links

            self._touch_and_save()
            return {
                "node_count": len(self._data["nodes"]),
                "link_count": len(self._data["links"]),
                "added_nodes": len(new_nodes),
                "removed_nodes": len(removed_node_ids),
                "added_links": len(new_links),
            }

    def all_nodes(self) -> list[DrosteNode]:
        with self._lock:
            self._maybe_reload()
            return self._nodes()

    def all_links(self) -> list[DrosteLink]:
        with self._lock:
            self._maybe_reload()
            return self._links()

    @staticmethod
    def normalize_root(root: str | Path | None) -> str | None:
        if root is None:
            return None
        clean = str(root).strip()
        if not clean:
            return None
        return str(Path(clean).expanduser().resolve())

    def set_active_root(self, root: str | Path | None) -> dict[str, Any]:
        with self._lock:
            self._maybe_reload()
            self._data["active_root"] = self.normalize_root(root)
            self._touch_and_save()
            return {
                "active_root": self._data.get("active_root"),
                "indexed_roots": self.indexed_roots(),
            }

    def active_root(self) -> str | None:
        with self._lock:
            self._maybe_reload()
            return self._data.get("active_root")

    def indexed_roots(self) -> list[str]:
        with self._lock:
            self._maybe_reload()
            roots = {
                self.normalize_root(node.index_root)
                for node in self._nodes()
                if node.index_root
            }
            return sorted(root for root in roots if root)

    def resolve_query_root(self, root: str | Path | None = None) -> tuple[str | None, str | None]:
        """Resolve the repo scope for agent-facing reads without mixing roots."""

        with self._lock:
            self._maybe_reload()
            roots = self.indexed_roots()
            explicit = self.normalize_root(root)
            if explicit:
                if roots and explicit not in roots:
                    return explicit, f"requested root is not indexed: {explicit}"
                return explicit, None

            active = self.normalize_root(self._data.get("active_root"))
            if active and (not roots or active in roots):
                return active, None
            if active and roots:
                return None, (
                    f"active_root is no longer indexed: {active}; "
                    "pass root explicitly or re-index the project"
                )
            if len(roots) == 1:
                return roots[0], None
            if len(roots) > 1:
                return None, (
                    "multiple indexed roots exist and no active_root is set; "
                    "pass root explicitly or run droste_index_project first"
                )
            return None, None

    def get_space_status(self) -> dict[str, Any]:
        with self._lock:
            self._maybe_reload()
            camera = self._camera()
            nodes = self._nodes()
            links = self._public_links({node.id for node in nodes})
            return {
                "node_count": len(nodes),
                "link_count": len(links),
                "camera": camera,
                "fov": self.calculate_fov(camera["zoom"]),
                "visible_nodes": self.visible_nodes(camera),
                "macro_nodes": [self._public_node(node) for node in nodes],
                "links": links,
                "updated_at": self._data.get("updated_at"),
                "database": str(self.db_path),
                "active_root": self._data.get("active_root"),
                "indexed_roots": self.indexed_roots(),
            }

    def ensure_sharded_storage(self) -> dict[str, Any]:
        """Persist the loaded graph using the current sharded storage format."""

        with self._lock:
            self._maybe_reload()
            if self._data.get("storage") == "sharded":
                shard_dir = self._shard_dir()
                return {
                    "status": "already_sharded",
                    "storage": "sharded",
                    "shard_dir": str(shard_dir),
                    "shard_count": (
                        sum(1 for _ in shard_dir.glob("*.json"))
                        if shard_dir.exists()
                        else 0
                    ),
                }

            previous = str(self._data.get("storage") or "inline")
            self._touch_and_save()
            self._data["storage"] = "sharded"
            self._loaded_mtime = self._db_mtime()
            shard_dir = self._shard_dir()
            return {
                "status": "migrated",
                "from": previous,
                "storage": "sharded",
                "shard_dir": str(shard_dir),
                "shard_count": (
                    sum(1 for _ in shard_dir.glob("*.json"))
                    if shard_dir.exists()
                    else 0
                ),
            }

    def get_visualizer_state(self) -> dict[str, Any]:
        with self._lock:
            self._maybe_reload()
            camera = self._camera()
            fov = self.calculate_fov(camera["zoom"])
            all_nodes = self._nodes()
            stream_nodes = [
                node for node in all_nodes
                if self._should_stream_to_visualizer(node, camera=camera, fov=fov)
            ]
            stream_nodes.sort(key=lambda node: self._stream_priority(node, camera, fov))
            if len(stream_nodes) > VISUALIZER_NODE_LIMIT:
                stream_nodes = stream_nodes[:VISUALIZER_NODE_LIMIT]

            stream_node_ids = {node.id for node in stream_nodes}
            links: list[dict[str, Any]] = []
            if camera["zoom"] >= VISUALIZER_LINK_ZOOM_THRESHOLD:
                all_nodes_by_id = {node.id: node for node in all_nodes}
                stream_nodes, links = self._expand_stream_with_wormholes(
                    stream_nodes=stream_nodes,
                    stream_node_ids=stream_node_ids,
                    all_nodes_by_id=all_nodes_by_id,
                    camera=camera,
                    fov=fov,
                )

            nodes = [
                self._visualizer_node(node, camera=camera, fov=fov)
                for node in stream_nodes
            ]
            visible_count = sum(1 for node in nodes if node["visible"])
            unlocked_count = sum(1 for node in nodes if node["detail_state"] == "unlocked")
            return {
                "camera": camera,
                "fov": fov,
                "node_count": len(all_nodes),
                "rendered_count": len(nodes),
                "link_count": len(links),
                "visible_count": visible_count,
                "unlocked_count": unlocked_count,
                "nodes": nodes,
                "links": links,
                "updated_at": self._data.get("updated_at"),
            }

    @staticmethod
    def calculate_fov(zoom_level: float) -> float:
        return 2.0 / max(0.1, float(zoom_level))

    def visible_nodes(self, camera: dict[str, float] | None = None) -> list[dict[str, Any]]:
        camera = camera or self._camera()
        fov = self.calculate_fov(camera["zoom"])
        visible = []
        for node in self._nodes():
            distance = self._distance(camera["x"], camera["y"], node.x, node.y)
            if distance <= fov and camera["zoom"] >= node.zoom_threshold:
                visible.append(self._node_view(node, camera=camera, fov=fov, distance=distance))
        return visible

    def _load_or_initialize(self) -> dict[str, Any]:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # Sharded-storage dirty oracle: shard_key (source_path) -> structural
        # fingerprint of that shard as of the last save. Reset on every (re)load
        # and re-primed from disk so the very first save after a load is already
        # incremental instead of a full re-shard.
        self._shard_fp: dict[str, str] = {}
        if not self.db_path.exists():
            data = self._empty_database()
            self._save(data)
            return data

        try:
            meta = json.loads(self.db_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            corrupt_path = self.db_path.with_name(f"{self.db_path.stem}.corrupt-{timestamp}.json")
            self.db_path.replace(corrupt_path)
            self._clear_shard_dir()
            data = self._empty_database()
            data["recovered_from"] = str(corrupt_path)
            self._save(data)
            return data

        if not isinstance(meta, dict):
            meta = self._empty_database()

        meta.setdefault("version", 1)
        meta.setdefault("camera", {"x": 0.0, "y": 0.0, "zoom": 1.0})
        meta.setdefault("links", [])
        meta.setdefault("updated_at", utc_now())
        meta.setdefault("active_root", None)

        # Reassemble the full node list in RAM. The sharded format keeps only
        # source_path-less "loose" nodes (injected concepts) inline; every indexed
        # file's nodes live in their own shard under .droste/nodes/. A legacy
        # monolithic db (no "storage" marker) keeps the whole graph inline and is
        # migrated to shards on the next save.
        if meta.get("storage") == "sharded":
            nodes: list[dict[str, Any]] = list(meta.get("nodes", []))
            shard_dir = self._shard_dir()
            if shard_dir.exists():
                for shard_file in shard_dir.glob("*.json"):
                    try:
                        payload = json.loads(shard_file.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        continue
                    if isinstance(payload, list):
                        nodes.extend(item for item in payload if isinstance(item, dict))
            meta["nodes"] = nodes
        else:
            meta.setdefault("nodes", [])

        # Prime per-shard fingerprints so unchanged shards are skipped on the very
        # first save of this process, not only on the second mutation.
        self._prime_shard_fingerprints(meta.get("nodes", []))
        return meta

    def _touch_and_save(self) -> None:
        self._data["updated_at"] = utc_now()
        self._save(self._data)

    def _shard_dir(self) -> Path:
        return self.db_path.parent.joinpath(*SHARD_SUBDIR)

    @staticmethod
    def _shard_filename(shard_key: str) -> str:
        return hashlib.sha1(shard_key.encode("utf-8")).hexdigest() + ".json"

    @staticmethod
    def _node_fingerprint(node: dict[str, Any]) -> tuple:
        # Everything that affects a node's serialized bytes EXCEPT the 384-float
        # embedding (fully determined by title+summary, both captured here) and the
        # cosmetic created_at/updated_at timestamps. updated_at is bumped on every
        # re-index even for untouched files, so including it would dirty every shard
        # and defeat the optimization. embedding length is kept so an empty->filled
        # lazy embedding still dirties the shard.
        def _r(value: Any) -> Any:
            return round(float(value), 6) if value is not None else None

        return (
            node.get("id"), node.get("title"), node.get("summary"),
            node.get("detail_content"), node.get("node_type"),
            node.get("parent_id"), node.get("source_path"),
            node.get("line_start"), node.get("line_end"),
            node.get("index_root"), node.get("content_hash"),
            _r(node.get("zoom_threshold")), _r(node.get("x")), _r(node.get("y")),
            _r(node.get("semantic_x")), _r(node.get("semantic_y")),
            _r(node.get("fixed_x")), _r(node.get("fixed_y")),
            tuple(node.get("children") or ()), len(node.get("embedding") or ()),
        )

    def _shard_fingerprint(self, node_dicts: list[dict[str, Any]]) -> str:
        hasher = hashlib.blake2b(digest_size=16)
        for node in sorted(node_dicts, key=lambda item: item.get("id") or ""):
            hasher.update(repr(self._node_fingerprint(node)).encode("utf-8"))
        return hasher.hexdigest()

    def _prime_shard_fingerprints(self, nodes: list[dict[str, Any]]) -> None:
        shards: dict[str, list[dict[str, Any]]] = {}
        for node in nodes:
            key = node.get("source_path")
            if key:
                shards.setdefault(key, []).append(node)
        self._shard_fp = {
            key: self._shard_fingerprint(group) for key, group in shards.items()
        }

    def _clear_shard_dir(self) -> None:
        shard_dir = self._shard_dir()
        if shard_dir.exists():
            for shard_file in shard_dir.glob("*.json"):
                try:
                    shard_file.unlink()
                except OSError:
                    pass

    @staticmethod
    def _atomic_write(path: Path, text: str) -> None:
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(text, encoding="utf-8")
        tmp_path.replace(path)

    def _save(self, data: dict[str, Any]) -> None:
        # Fragmented persistence with file-level dirty-tracking: each indexed
        # source file's nodes live in their own shard (sha1(source_path).json)
        # under .droste/nodes/. A save only re-serializes shards whose structural
        # fingerprint changed — the warm re-index path (e.g. 2 of 100 files
        # mutated) writes 2 small files instead of json.dumps-ing 1.8M dicts for
        # the entire graph, which was the old 2.85s O(N) wall.
        shard_dir = self._shard_dir()
        shard_dir.mkdir(parents=True, exist_ok=True)

        shards: dict[str, list[dict[str, Any]]] = {}
        loose: list[dict[str, Any]] = []
        for node in data.get("nodes", []):
            key = node.get("source_path")
            if key:
                shards.setdefault(key, []).append(node)
            else:
                loose.append(node)

        live_files: set[str] = set()
        new_fp: dict[str, str] = {}
        for key, group in shards.items():
            filename = self._shard_filename(key)
            live_files.add(filename)
            fingerprint = self._shard_fingerprint(group)
            new_fp[key] = fingerprint
            shard_path = shard_dir / filename
            if self._shard_fp.get(key) == fingerprint and shard_path.exists():
                continue  # clean shard: skip json.dumps + disk write entirely
            self._atomic_write(
                shard_path, json.dumps(group, ensure_ascii=False, indent=2)
            )
        self._shard_fp = new_fp

        # Reap shards for files no longer in the graph (deleted / re-rooted / post
        # reset) so stale nodes can't resurrect on the next reload.
        for shard_file in shard_dir.glob("*.json"):
            if shard_file.name not in live_files:
                try:
                    shard_file.unlink()
                except OSError:
                    pass

        # Meta holds everything light (camera, links, version, loose nodes) and is
        # rewritten on every save so its mtime stays the cross-process staleness
        # signal. It is written LAST: once its mtime advances, every shard it
        # implies is already durably on disk.
        meta = {key: value for key, value in data.items() if key != "nodes"}
        meta["storage"] = "sharded"
        meta["nodes"] = loose
        self._atomic_write(
            self.db_path, json.dumps(meta, ensure_ascii=False, indent=2)
        )

        # Our own write: advance the marker so _maybe_reload doesn't clobber the
        # in-RAM state we just persisted.
        self._loaded_mtime = self._db_mtime()

    def _db_mtime(self) -> float:
        try:
            return self.db_path.stat().st_mtime
        except OSError:
            return 0.0

    def _maybe_reload(self) -> None:
        """Reload from disk if another process wrote a newer db.

        Safe because every mutation persists immediately (in-RAM == last save),
        so there is never unsaved state to lose. Called on read paths under the
        lock so cross-process re-indexes become visible without a restart.
        """
        disk_mtime = self._db_mtime()
        if disk_mtime > self._loaded_mtime + 1e-6:
            try:
                self._data = self._load_or_initialize()
                self._loaded_mtime = disk_mtime
            except Exception:
                pass

    @staticmethod
    def _empty_database() -> dict[str, Any]:
        return {
            "version": 1,
            "camera": {"x": 0.0, "y": 0.0, "zoom": 1.0},
            "nodes": [],
            "links": [],
            "active_root": None,
            "updated_at": utc_now(),
        }

    def _nodes(self) -> list[DrosteNode]:
        nodes_data = self._data.get("nodes", [])
        key = id(nodes_data)
        if self._nodes_cache is None or self._nodes_cache_key != key:
            self._nodes_cache = [DrosteNode.from_dict(item) for item in nodes_data]
            self._nodes_cache_key = key
        # Shallow-copy the list so callers (e.g. inject_concept's append) cannot
        # mutate the cached list; the DrosteNode objects are shared, which only
        # helps lazy embedding fills persist into the next save.
        return list(self._nodes_cache)

    def _links(self) -> list[DrosteLink]:
        links_data = self._data.get("links", [])
        key = id(links_data)
        if self._links_cache is None or self._links_cache_key != key:
            parsed: list[DrosteLink] = []
            for item in links_data:
                if not isinstance(item, dict):
                    continue
                link = DrosteLink.from_dict(item)
                if link.from_node and link.to_node:
                    parsed.append(link)
            self._links_cache = parsed
            self._links_cache_key = key
        return list(self._links_cache)

    def _camera(self) -> dict[str, float]:
        raw = self._data.get("camera", {})
        return {
            "x": clamp(float(raw.get("x", 0.0)), -1.0, 1.0),
            "y": clamp(float(raw.get("y", 0.0)), -1.0, 1.0),
            "zoom": max(0.1, float(raw.get("zoom", 1.0))),
        }

    def _embedding_for_node(self, node: DrosteNode) -> list[float]:
        if node.embedding:
            return node.embedding
        node.embedding = self.projector.embed_text(f"{node.title}\n{node.summary}")
        return node.embedding

    @staticmethod
    def _unique_node_id(base_id: str, nodes: list[DrosteNode]) -> str:
        existing_ids = {node.id for node in nodes}
        if base_id not in existing_ids:
            return base_id

        suffix = 2
        while f"{base_id}-{suffix}" in existing_ids:
            suffix += 1
        return f"{base_id}-{suffix}"

    @staticmethod
    def _distance(x1: float, y1: float, x2: float, y2: float) -> float:
        return math.sqrt((x1 - x2) ** 2 + (y1 - y2) ** 2)

    @staticmethod
    def _normalize_links(
        links: list[dict[str, Any] | DrosteLink],
        index_root: str,
    ) -> list[DrosteLink]:
        normalized: list[DrosteLink] = []
        seen: set[tuple[str, str, str, str]] = set()
        for item in links:
            link = item if isinstance(item, DrosteLink) else DrosteLink.from_dict(item)
            if not link.from_node or not link.to_node or link.from_node == link.to_node:
                continue
            link.index_root = index_root
            link.updated_at = utc_now()
            key = (link.from_node, link.to_node, link.type, link.label or "")
            if key in seen:
                continue
            seen.add(key)
            normalized.append(link)
        return normalized

    def _should_stream_to_visualizer(
        self,
        node: DrosteNode,
        camera: dict[str, float],
        fov: float,
    ) -> bool:
        distance = self._distance(camera["x"], camera["y"], node.x, node.y)
        zoom = camera["zoom"]

        if node.node_type == "project":
            return distance <= max(fov * 1.2, 0.35)

        if distance > fov * 1.12 + 0.06:
            return False

        if zoom >= node.zoom_threshold:
            return True

        preview_gate = {
            "directory": 0.84,
            "file": 0.9,
            "symbol": 0.97,
            "section": 0.97,
            "concept": 0.78,
        }.get(node.node_type, 0.9)
        return zoom >= node.zoom_threshold * preview_gate and distance <= fov

    def _stream_priority(
        self,
        node: DrosteNode,
        camera: dict[str, float],
        fov: float,
    ) -> tuple[int, float, float, str]:
        distance = self._distance(camera["x"], camera["y"], node.x, node.y)
        unlocked = camera["zoom"] >= node.zoom_threshold
        type_rank = {
            "project": 0,
            "concept": 1,
            "directory": 2,
            "file": 3,
            "symbol": 4,
            "section": 4,
        }.get(node.node_type, 5)
        focus = distance / max(fov, 1e-9)
        return (0 if unlocked else 1, focus, float(type_rank), node.id)

    def _expand_stream_with_wormholes(
        self,
        stream_nodes: list[DrosteNode],
        stream_node_ids: set[str],
        all_nodes_by_id: dict[str, DrosteNode],
        camera: dict[str, float],
        fov: float,
    ) -> tuple[list[DrosteNode], list[dict[str, Any]]]:
        expanded_nodes = list(stream_nodes)
        links: list[dict[str, Any]] = []
        added_neighbors = 0

        for link in self._links():
            source_in_view = link.from_node in stream_node_ids
            target_in_view = link.to_node in stream_node_ids
            if not source_in_view and not target_in_view:
                continue

            source = all_nodes_by_id.get(link.from_node)
            target = all_nodes_by_id.get(link.to_node)
            if not source or not target:
                continue

            if link.from_node not in stream_node_ids:
                if added_neighbors >= VISUALIZER_WORMHOLE_NEIGHBOR_LIMIT:
                    continue
                stream_node_ids.add(link.from_node)
                expanded_nodes.append(source)
                added_neighbors += 1
            if link.to_node not in stream_node_ids:
                if added_neighbors >= VISUALIZER_WORMHOLE_NEIGHBOR_LIMIT:
                    continue
                stream_node_ids.add(link.to_node)
                expanded_nodes.append(target)
                added_neighbors += 1

            links.append(link.to_dict())
            if len(links) >= VISUALIZER_LINK_LIMIT:
                break

        expanded_nodes.sort(key=lambda node: self._stream_priority(node, camera, fov))
        if len(expanded_nodes) > VISUALIZER_NODE_LIMIT:
            kept_ids = {node.id for node in expanded_nodes[:VISUALIZER_NODE_LIMIT]}
            links = [
                link for link in links
                if str(link.get("from")) in kept_ids and str(link.get("to")) in kept_ids
            ]
            expanded_nodes = expanded_nodes[:VISUALIZER_NODE_LIMIT]
        return expanded_nodes, links

    @staticmethod
    def _public_node(node: DrosteNode) -> dict[str, Any]:
        semantic_x = node.semantic_x if node.semantic_x is not None else node.x
        semantic_y = node.semantic_y if node.semantic_y is not None else node.y
        if node.index_root and node.fixed_x is not None and node.fixed_y is not None:
            coordinate_mode = "radial"
        elif node.fixed_x is not None and node.fixed_y is not None:
            coordinate_mode = "fixed"
        else:
            coordinate_mode = "projected"
        return {
            "id": node.id,
            "title": node.title,
            "summary": node.summary,
            "node_type": node.node_type,
            "parent_id": node.parent_id,
            "children": list(node.children),
            "source_path": node.source_path,
            "line_start": node.line_start,
            "line_end": node.line_end,
            "index_root": node.index_root,
            "content_hash": node.content_hash,
            "x": node.x,
            "y": node.y,
            "semantic_x": semantic_x,
            "semantic_y": semantic_y,
            "coordinate_mode": coordinate_mode,
            "zoom_threshold": node.zoom_threshold,
            "created_at": node.created_at,
            "updated_at": node.updated_at,
        }

    def _public_links(
        self,
        node_ids: set[str] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        links = []
        for link in self._links():
            if node_ids and (link.from_node not in node_ids or link.to_node not in node_ids):
                continue
            links.append(link.to_dict())
            if limit is not None and len(links) >= limit:
                break
        return links

    def _node_view(
        self,
        node: DrosteNode,
        camera: dict[str, float],
        fov: float,
        distance: float | None = None,
    ) -> dict[str, Any]:
        distance = distance if distance is not None else self._distance(
            camera["x"], camera["y"], node.x, node.y
        )
        unlocked = camera["zoom"] >= node.zoom_threshold
        focus = clamp(1.0 - (distance / max(fov, 1e-9)), 0.0, 1.0)
        return {
            **self._public_node(node),
            "distance": distance,
            "focus": focus,
            "detail_state": "unlocked" if unlocked else "locked",
            "detail_content": node.detail_content if unlocked else LOCKED_DETAIL,
        }

    def _visualizer_node(
        self,
        node: DrosteNode,
        camera: dict[str, float],
        fov: float,
    ) -> dict[str, Any]:
        distance = self._distance(camera["x"], camera["y"], node.x, node.y)
        spatially_visible = distance <= fov
        zoom_visible = camera["zoom"] >= node.zoom_threshold
        visible = spatially_visible and zoom_visible
        unlocked = visible
        focus = clamp(1.0 - (distance / max(fov, 1e-9)), 0.0, 1.0) if visible else 0.0
        detail = node.detail_content if unlocked else LOCKED_DETAIL
        if unlocked:
            detail_state = "unlocked"
        elif spatially_visible:
            detail_state = "locked"
        else:
            detail_state = "outside_fov"
        return {
            **self._public_node(node),
            "distance": distance,
            "visible": visible,
            "focus": focus,
            "detail_state": detail_state,
            "detail_content": detail,
            "detail_preview": detail[:260],
        }
