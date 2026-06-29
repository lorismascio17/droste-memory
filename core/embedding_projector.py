"""Semantic embedding and 2D projection utilities for Droste-Memory."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from typing import Sequence

try:
    import numpy as np
except Exception:  # pragma: no cover - optional dependency fallback
    np = None  # type: ignore[assignment]

try:
    from sklearn.decomposition import PCA
except Exception:  # pragma: no cover - optional dependency fallback
    PCA = None  # type: ignore[assignment]


Vector = list[float]
Point = tuple[float, float]
TOKEN_PATTERN = re.compile(r"[\w']+", re.UNICODE)


@dataclass(frozen=True)
class ProjectionResult:
    """Result of projecting a set of embeddings into canvas coordinates."""

    coordinates: list[Point]
    method: str
    warning: str | None = None


class EmbeddingProjector:
    """Create embeddings and project them onto the Droste 2D canvas.

    The preferred path uses sentence-transformers/all-MiniLM-L6-v2 plus PCA.
    If the model or sklearn is unavailable, the class falls back to a
    deterministic token-hash embedding and a min-max 2D projection so the
    project stays runnable in a fresh local checkout.
    """

    # fastembed (ONNX) is the preferred real-semantics backend: 384-dim, no
    # torch, runs fully local -> protects the zero-config moat while unlocking
    # true synonym recall the deterministic hash fallback cannot give.
    FASTEMBED_MODEL = "BAAI/bge-small-en-v1.5"

    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2") -> None:
        self.model_name = model_name
        self._model = None  # sentence-transformers model, if present
        self._fastembed = None  # fastembed TextEmbedding, if present
        self._backend = "hash"  # resolved on first _load_model()
        self._model_checked = False
        self._model_error: str | None = None

    @property
    def model_error(self) -> str | None:
        return self._model_error

    @property
    def backend(self) -> str:
        self._load_model()
        return self._backend

    def embed_text(self, text: str) -> Vector:
        normalized_text = (text or "").strip() or "empty concept"
        self._load_model()

        if self._fastembed is not None:
            try:
                vector = next(iter(self._fastembed.embed([normalized_text])))
                if hasattr(vector, "tolist"):
                    vector = vector.tolist()
                return self._l2_normalize([float(value) for value in vector])
            except Exception as exc:  # pragma: no cover - depends on local state
                self._model_error = f"fastembed: {type(exc).__name__}: {exc}"

        if self._model is not None:
            try:
                embedding = self._model.encode(normalized_text, normalize_embeddings=True)
                if hasattr(embedding, "tolist"):
                    embedding = embedding.tolist()
                return [float(value) for value in embedding]
            except Exception as exc:  # pragma: no cover - depends on local model state
                self._model_error = f"{type(exc).__name__}: {exc}"

        return self._fallback_embedding(normalized_text)

    def embed_texts(self, texts: Sequence[str]) -> list[Vector]:
        """Batch embedding — the throughput path used at index time.

        fastembed (ONNX) processes a whole list in one vectorised pass with an
        internal batch loop, which is dramatically faster than calling
        ``embed_text`` N times (each call re-enters the runtime). ``parallel=0``
        lets fastembed fan out across all CPU cores for large inputs. Falls back
        to per-text embedding (sentence-transformers / hash) when fastembed is
        unavailable so behaviour is identical, only slower."""
        items = list(texts)
        if not items:
            return []
        normalized = [(text or "").strip() or "empty concept" for text in items]
        self._load_model()

        if self._fastembed is not None:
            try:
                # parallel=0 -> use all cores; only worth the process spin-up
                # cost on larger batches (Windows process start is not free).
                parallel = 0 if len(normalized) >= 256 else None
                out: list[Vector] = []
                for vector in self._fastembed.embed(normalized, batch_size=256, parallel=parallel):
                    if hasattr(vector, "tolist"):
                        vector = vector.tolist()
                    out.append(self._l2_normalize([float(value) for value in vector]))
                if len(out) == len(normalized):
                    return out
                # length mismatch -> something went wrong; fall through.
            except Exception as exc:  # pragma: no cover - depends on local state
                self._model_error = f"fastembed batch: {type(exc).__name__}: {exc}"

        return [self.embed_text(text) for text in normalized]

    @staticmethod
    def _l2_normalize(vector: Vector) -> Vector:
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]

    def project_embeddings(self, embeddings: Sequence[Sequence[float]]) -> ProjectionResult:
        vectors = [list(vector) for vector in embeddings]
        if not vectors:
            return ProjectionResult(coordinates=[], method="empty")

        if len(vectors) == 1:
            return ProjectionResult(
                coordinates=[(0.0, 0.0)],
                method="origin",
                warning=self._model_error,
            )

        if np is not None and PCA is not None:
            try:
                matrix = np.asarray(vectors, dtype=float)
                if matrix.ndim != 2:
                    raise ValueError("embedding matrix must be two-dimensional")
                if matrix.shape[0] < 2:
                    raise ValueError("at least two embeddings are required for PCA")
                if np.allclose(matrix, matrix[0]):
                    raise ValueError("all embeddings are identical")

                coords = PCA(n_components=2, random_state=0).fit_transform(matrix)
                coords = np.nan_to_num(coords, copy=False)
                scaled = self._scale_numpy_points(coords)
                return ProjectionResult(
                    coordinates=[(float(x), float(y)) for x, y in scaled],
                    method="pca",
                    warning=self._model_error,
                )
            except Exception as exc:
                warning = f"PCA fallback active: {type(exc).__name__}: {exc}"
                if self._model_error:
                    warning = f"{warning}; embedding warning: {self._model_error}"
                return ProjectionResult(
                    coordinates=self._fallback_project(vectors),
                    method="fallback",
                    warning=warning,
                )

        warning = "PCA fallback active: numpy or scikit-learn is not installed"
        if self._model_error:
            warning = f"{warning}; embedding warning: {self._model_error}"
        return ProjectionResult(
            coordinates=self._fallback_project(vectors),
            method="fallback",
            warning=warning,
        )

    def _load_model(self) -> None:
        if self._model_checked:
            return
        self._model_checked = True

        # Make Python trust the OS cert store (Windows/corporate root CAs) so the
        # one-time model fetch over httpx doesn't die on CERTIFICATE_VERIFY_FAILED.
        # Best-effort: silently skipped if truststore isn't installed.
        try:
            import truststore

            truststore.inject_into_ssl()
        except Exception:  # pragma: no cover - optional hardening
            pass

        # 1) Preferred: fastembed (ONNX, no torch) -> real semantics, moat-safe.
        try:
            from fastembed import TextEmbedding

            self._fastembed = TextEmbedding(model_name=self.FASTEMBED_MODEL)
            self._backend = "fastembed"
            return
        except Exception as exc:  # pragma: no cover - depends on installed packages
            self._fastembed = None
            self._model_error = f"fastembed unavailable: {type(exc).__name__}: {exc}"

        # 2) Fallback: sentence-transformers (heavier, pulls torch).
        try:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name)
            self._backend = "sentence_transformers"
        except Exception as exc:  # pragma: no cover - depends on installed packages
            self._model = None
            self._backend = "hash"
            self._model_error = f"{type(exc).__name__}: {exc}"

    @staticmethod
    def _fallback_embedding(text: str, dimensions: int = 384) -> Vector:
        tokens = TOKEN_PATTERN.findall(text.lower()) or [text.lower()]
        vector = [0.0] * dimensions

        for token in tokens:
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=32).digest()
            weight = 1.0 + min(len(token), 32) / 32.0
            for index in range(0, len(digest), 2):
                slot = int.from_bytes(digest[index : index + 2], "big") % dimensions
                sign = 1.0 if digest[index] & 1 else -1.0
                vector[slot] += sign * weight

        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]

    @staticmethod
    def _fallback_project(vectors: Sequence[Sequence[float]]) -> list[Point]:
        raw_points: list[Point] = []
        for vector in vectors:
            x = float(vector[0]) if len(vector) > 0 else 0.0
            y = float(vector[1]) if len(vector) > 1 else 0.0
            raw_points.append((x, y))

        return EmbeddingProjector._scale_plain_points(raw_points)

    @staticmethod
    def _scale_numpy_points(coords: "np.ndarray") -> "np.ndarray":  # type: ignore[name-defined]
        centered = coords - coords.mean(axis=0, keepdims=True)
        max_abs = float(np.max(np.abs(centered)))  # type: ignore[union-attr]
        if max_abs <= 1e-12:
            return np.asarray(EmbeddingProjector._radial_layout(len(coords)), dtype=float)  # type: ignore[union-attr]
        return np.clip(centered / max_abs, -1.0, 1.0)  # type: ignore[union-attr]

    @staticmethod
    def _scale_plain_points(points: Sequence[Point]) -> list[Point]:
        if not points:
            return []

        mean_x = sum(x for x, _ in points) / len(points)
        mean_y = sum(y for _, y in points) / len(points)
        centered = [(x - mean_x, y - mean_y) for x, y in points]
        max_abs = max(max(abs(x), abs(y)) for x, y in centered)

        if max_abs <= 1e-12:
            return EmbeddingProjector._radial_layout(len(points))

        return [
            (max(-1.0, min(1.0, x / max_abs)), max(-1.0, min(1.0, y / max_abs)))
            for x, y in centered
        ]

    @staticmethod
    def _radial_layout(count: int) -> list[Point]:
        if count <= 1:
            return [(0.0, 0.0)] if count == 1 else []

        radius = 0.65
        return [
            (
                radius * math.cos((2.0 * math.pi * index) / count),
                radius * math.sin((2.0 * math.pi * index) / count),
            )
            for index in range(count)
        ]
