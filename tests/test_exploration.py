"""Complete-response token budgets, progressive detail and retained-content receipts."""
import json
import os

import pytest

from core.exploration import explore, tokenizer
from core.inspection import find_symbols


@pytest.fixture
def indexed(tmp_path, engine, ingester):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "logic.py").write_text(
        "class Ops:\n"
        "    def run(self, value):\n"
        "        '''Process città 東京 and literal <|endoftext|> safely.'''\n"
        "        return self.helper(value)\n"
        "    def helper(self, value):\n"
        "        return value + 1\n", encoding="utf-8")
    ingester.index_project(root)
    return root


@pytest.mark.parametrize("encoding", ["o200k_base", "cl100k_base"])
@pytest.mark.parametrize("level", ["map", "structure", "source"])
@pytest.mark.parametrize("budget", [256, 512, 2000])
def test_entire_wire_text_respects_tokens_with_unicode_and_special_literals(engine, indexed, encoding, level, budget):
    text = explore(engine, focus="Ops.run", level=level, tokens=budget, encoding=encoding)
    packet = json.loads(text)
    assert len(tokenizer(encoding).encode_ordinary(text)) == packet["used"] <= budget
    assert text.endswith("\n")
    if packet["items"] and packet["items"][0]["level"] == "source":
        assert "return self.helper(value)" in packet["items"][0]["text"]
        assert "東京" in packet["items"][0]["text"]


def test_zoom_reveals_structure_then_exact_source(engine, indexed):
    overview = json.loads(explore(engine, focus="Ops.run", level="map"))
    target = overview["items"][0]["id"]
    assert all("text" not in item for item in overview["items"])
    structure = json.loads(explore(engine, focus=target, level="structure"))
    item = structure["items"][0]
    assert item["level"] == "structure"
    assert "def run(self, value):" in item["text"]
    assert "return self.helper" not in item["text"]
    detail = json.loads(explore(engine, focus=target, level="source"))
    assert detail["items"][0]["level"] == "source"
    assert "4:         return self.helper(value)" in detail["items"][0]["text"]
    assert detail["items"][0]["path"] == "logic.py"
    assert any(link[0] == 0 for link in detail["links"])


def test_receipts_do_not_confuse_a_map_with_the_source(engine, indexed):
    overview = json.loads(explore(engine, focus="Ops.run", level="map"))
    seen = [item["handle"] for item in overview["items"]]
    detail = json.loads(explore(engine, focus="Ops.run", level="source", seen=seen))
    assert detail["items"][0]["level"] == "source"
    assert detail["items"][0]["handle"] not in seen


def test_unchanged_source_is_reused_only_when_acknowledged(engine, ingester, tmp_path):
    root = tmp_path / "receipt"
    root.mkdir()
    (root / "big.py").write_text("def calculate(value):\n" + "".join(f"    value += {i}\n" for i in range(50)) + "    return value\n", encoding="utf-8")
    ingester.index_project(root)
    first = json.loads(explore(engine, focus="calculate", level="source", tokens=2000))
    seen = [item["handle"] for item in first["items"]]
    repeated = json.loads(explore(engine, focus="calculate", level="source", tokens=2000, seen=seen))
    assert repeated["items"] == []
    assert repeated["reused"][0]["level"] == "source"
    assert repeated["used"] < first["used"]
    reset = json.loads(explore(engine, focus="calculate", level="source", tokens=2000))
    assert reset["items"][0]["handle"] == seen[0]


def test_same_size_same_mtime_edits_cannot_reuse_receipts(engine, ingester, indexed):
    first = json.loads(explore(engine, focus="Ops.run", level="source"))
    path = indexed / "logic.py"
    stat = path.stat()
    source = path.read_bytes()
    path.write_bytes(source.replace(b"value + 1", b"value + 9"))
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    stale = json.loads(explore(engine, focus="Ops.run", level="source", seen=[i["handle"] for i in first["items"]]))
    assert stale["status"] == "needs_index"
    assert not stale["items"] and not stale["reused"]
    ingester.index_project(indexed)
    current = json.loads(explore(engine, focus="Ops.run", level="source", seen=[i["handle"] for i in first["items"]]))
    assert current["items"][0]["handle"] != first["items"][0]["handle"]


def test_partial_indexes_warn_and_other_roots_cannot_leak(engine, ingester, indexed, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    (other / "elsewhere.py").write_text("def elsewhere(): pass\n", encoding="utf-8")
    ingester.index_project(other, max_symbols=1)
    target = find_symbols(engine, "Ops.run", root=indexed)["matches"][0]["id"]
    result = json.loads(explore(engine, focus=target, root=str(other)))
    assert result["status"] == "not_found"
    assert "logic.py" not in json.dumps(result)
    ingester.index_project(indexed, max_symbols=1)
    partial = json.loads(explore(engine, root=str(indexed)))
    assert partial["health"] == "partial"
    assert "index_coverage_is_partial" in partial["warnings"]


def test_ambiguous_focus_returns_maps_without_selecting_a_body(engine, ingester, tmp_path):
    root = tmp_path / "ambiguous"
    root.mkdir()
    for name in ("a.py", "b.py"):
        (root / name).write_text("def send(): pass\n", encoding="utf-8")
    ingester.index_project(root)
    result = json.loads(explore(engine, focus="send", level="source"))
    assert result["status"] == "ambiguous"
    assert "focus" not in result
    assert len(result["items"]) == 2
    assert all(item["level"] == "map" for item in result["items"])


def test_large_source_downgrades_without_slicing_code(engine, ingester, tmp_path):
    root = tmp_path / "large"
    root.mkdir()
    (root / "large.py").write_text("def huge():\n    return " + repr("東京" * 6000) + "\n", encoding="utf-8")
    ingester.index_project(root)
    result = json.loads(explore(engine, focus="huge", level="source", tokens=512))
    assert result["items"][0]["level"] == "structure"
    assert "implementation omitted" in result["items"][0]["text"]
    assert "東京" not in result["items"][0]["text"]


def test_edit_after_snapshot_check_cannot_leak_old_spans(engine, indexed, monkeypatch):
    from core import exploration
    actual_check = exploration.check_index
    def race(*args, **kwargs):
        result = actual_check(*args, **kwargs)
        path = indexed / "logic.py"
        path.write_text(path.read_text(encoding="utf-8").replace("value + 1", "value + 9"), encoding="utf-8")
        return result
    monkeypatch.setattr(exploration, "check_index", race)
    packet = json.loads(exploration.explore(engine, focus="Ops.run", level="source"))
    assert packet["status"] == "needs_index"
    assert not packet["items"] and not packet["reused"]


def test_file_structure_represents_the_module_instead_of_only_its_first_function(engine, ingester, tmp_path):
    root = tmp_path / "module"
    root.mkdir()
    (root / "module.py").write_text("def first():\n    return 1\ndef second():\n    return 2\n", encoding="utf-8")
    ingester.index_project(root)
    file = next(n for n in engine.all_nodes() if n.node_type == "file")
    packet = json.loads(explore(engine, focus=file.id, level="structure", tokens=2000))
    assert packet["items"][0]["level"] == "structure"
    assert "def first():" in packet["items"][0]["text"]
    assert "def second():" in packet["items"][0]["text"]
    assert "return 1" not in packet["items"][0]["text"]


@pytest.mark.parametrize("kwargs", [{"tokens": 255}, {"tokens": 64001}, {"tokens": True},
                                    {"level": "infinite"}, {"encoding": "guess"}, {"seen": ["anything"]}])
def test_invalid_exploration_requests_are_rejected(engine, kwargs):
    with pytest.raises(ValueError):
        explore(engine, **kwargs)
