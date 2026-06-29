"""Root pytest conftest.

Lives at the repo root so its directory is prepended to sys.path (pytest
prepend import mode) — that makes `import core...` resolve when the suite runs
from anywhere. Also centralizes the isolated fixtures every test reuses.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from core.droste_engine import DrosteConceptEngine          # noqa: E402
from core.droste_ingester import DrosteProjectIngester      # noqa: E402


def force_hash_backend(engine: DrosteConceptEngine) -> None:
    """Pin the deterministic token-hash embedding backend.

    Every test must be reproducible offline and independent of whether
    fastembed/torch happen to be installed, so we never let the projector reach
    for an ONNX model. The hash fallback is fully deterministic.
    """
    proj = engine.projector
    proj._model_checked = True
    proj._backend = "hash"
    proj._fastembed = None
    proj._model = None


@pytest.fixture
def engine(tmp_path: Path) -> DrosteConceptEngine:
    """A fresh engine on an isolated temp DB (its own .droste/nodes/ shard dir)."""
    eng = DrosteConceptEngine(db_path=tmp_path / "db.json")
    force_hash_backend(eng)
    return eng


@pytest.fixture
def ingester(engine: DrosteConceptEngine) -> DrosteProjectIngester:
    return DrosteProjectIngester(engine)


@pytest.fixture
def sample_project(tmp_path: Path) -> Path:
    """A tiny multi-file Python project with a shared hub symbol, so get_context
    has real syntactic wormholes (caller/callee) to pack."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "common.py").write_text(
        '"""Shared hub used across the package."""\n\n\n'
        "def hub_core(value):\n"
        '    """Central hub every module funnels through for crowding."""\n'
        "    return transform(value) + 1\n\n\n"
        "def transform(value):\n"
        '    """Identity-ish transform used everywhere."""\n'
        "    return value\n",
        encoding="utf-8",
    )
    for i in range(4):
        lines = ['"""Module %02d."""' % i, "from common import hub_core, transform", ""]
        for j in range(6):
            lines += [
                f"def mod{i}_op{j}(payload):",
                f'    """Module {i} operation {j} that leans on the shared hub."""',
                "    staged = transform(payload)",
                "    primed = hub_core(staged)",
                "    return hub_core(primed) + transform(staged)",
                "",
            ]
        (root / f"module_{i:02d}.py").write_text("\n".join(lines), encoding="utf-8")
    return root
