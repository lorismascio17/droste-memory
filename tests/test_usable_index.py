"""User-facing correctness: source resolution, fresh indexes and useful queries."""
import os
from pathlib import Path

import pytest

from core.droste_engine import DrosteConceptEngine
from core.droste_ingester import DrosteProjectIngester
from core.droste_watcher import ReactiveWatcher
from core.inspection import check_index, find_symbols, references
from conftest import force_hash_backend


def project(tmp_path, files):
    root = tmp_path / "project"
    root.mkdir(exist_ok=True)
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


def edges(engine, root):
    nodes = {n.id: n for n in engine.all_nodes()}
    return {(Path(nodes[l.from_node].source_path).relative_to(root).as_posix(), nodes[l.from_node].line_start,
             Path(nodes[l.to_node].source_path).relative_to(root).as_posix(), nodes[l.to_node].line_start)
            for l in engine.all_links() if l.type == "syntax_dependency"}


def test_same_named_external_function_cannot_steal_local_call(ingester, engine, tmp_path):
    root = project(tmp_path, {"a.py": "def helper(x):\n    return x - 10\n",
        "b.py": "def helper(x):\n    return x * 2\n\ndef calculate(x):\n    return helper(x)\n"})
    ingester.index_project(root)
    assert edges(engine, root) == {("b.py", 4, "b.py", 1)}
    result = references(engine, "calculate", direction="callees")
    assert result["references"][0]["to"]["path"] == "b.py"
    assert result["references"][0]["evidence"][0]["line"] == 5
    reloaded = DrosteConceptEngine(engine.db_path)
    assert reloaded.all_links()[0].evidence == engine.all_links()[0].evidence


@pytest.mark.parametrize("statement,call", [
    ("from a import helper as work", "work()"),
    ("import a as module", "module.helper()"),
    ("import a", "a.helper()"),
])
def test_import_bindings_choose_declared_module(ingester, engine, tmp_path, statement, call):
    root = project(tmp_path, {"a.py": "def helper():\n    return 1\n", "c.py": "def helper():\n    return 2\n",
        "b.py": statement + "\ndef caller():\n    return " + call + "\n"})
    ingester.index_project(root)
    assert edges(engine, root) == {("b.py", 2, "a.py", 1)}


def test_relative_import_reexports_and_namespace_packages(ingester, engine, tmp_path):
    root = project(tmp_path, {"src/pkg/__init__.py": "from .service import helper\n",
        "src/pkg/service.py": "def helper():\n    return 42\n",
        "src/pkg/api.py": "from . import helper as local\n\ndef endpoint():\n    return local()\n",
        "main.py": "import pkg.service\n\ndef run():\n    return pkg.service.helper()\n"})
    ingester.index_project(root)
    assert ("src/pkg/api.py", 3, "src/pkg/service.py", 1) in edges(engine, root)
    assert ("main.py", 3, "src/pkg/service.py", 1) in edges(engine, root)


def test_dynamic_receiver_and_shadowed_parameter_do_not_fabricate_links(ingester, engine, tmp_path):
    root = project(tmp_path, {"a.py": "def send():\n    return 1\n",
        "b.py": "from a import send\n\ndef shadow(send):\n    return send()\n\ndef dynamic(client):\n    return client.send()\n"})
    ingester.index_project(root)
    assert edges(engine, root) == set()


def test_methods_and_nested_functions_use_their_own_scope(ingester, engine, tmp_path):
    root = project(tmp_path, {"service.py": "class A:\n    def work(self):\n        return self.helper()\n    def helper(self):\n        return 1\n\nclass B:\n    def helper(self):\n        return 2\n\ndef outer():\n    def inner():\n        return outer()\n    return inner()\n"})
    ingester.index_project(root)
    actual = edges(engine, root)
    assert ("service.py", 2, "service.py", 4) in actual
    assert ("service.py", 2, "service.py", 8) not in actual
    assert ("service.py", 1, "service.py", 4) not in actual  # class isn't a caller of its method body
    assert ("service.py", 11, "service.py", 12) in actual
    assert find_symbols(engine, "A.helper")["matches"][0]["line_start"] == 4


def test_identical_code_in_two_modules_is_not_deduplicated(ingester, engine, tmp_path):
    root = project(tmp_path, {"a.py": "def helper():\n    return 1\n", "b.py": "def helper():\n    return 1\n"})
    ingester.index_project(root)
    assert find_symbols(engine, "helper")["total"] == 2
    assert references(engine, "helper")["status"] == "ambiguous"
    assert references(engine, "helper", file="b.py")["target"]["path"] == "b.py"


def test_ambiguous_non_python_reference_is_not_guessed(ingester, engine, tmp_path):
    root = project(tmp_path, {"a.ts": "export function helper() { return 1; }",
        "b.ts": "export function helper() { return 2; }", "c.ts": "function run() { return helper(); }"})
    result = ingester.index_project(root)
    assert edges(engine, root) == set()
    assert result["resolution"]["ambiguous_name_links_skipped"] > 0


def test_freshness_hashes_detect_changes_even_if_size_and_mtime_preserved(ingester, engine, tmp_path):
    root = project(tmp_path, {"a.py": "def one():\n    return 1\n"})
    ingester.index_project(root)
    assert check_index(engine)["status"] == "fresh"
    path = root / "a.py"
    stat = path.stat()
    path.write_text("def one():\n    return 2\n", encoding="utf-8")
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert check_index(engine)["changed"] == ["a.py"]
    ingester.index_project(root)
    path.unlink()
    (root / "b.py").write_text("def two():\n    return 2\n", encoding="utf-8")
    report = check_index(engine)
    assert report["added"] == ["b.py"] and report["deleted"] == ["a.py"]
    assert ingester.get_context("one")["warnings"]


def test_partial_indexes_cannot_report_complete_coverage_and_warm_limits_hold(ingester, engine, tmp_path):
    root = project(tmp_path, {"a.py": "def one(): pass\ndef two(): pass\n", "b.py": "def three(): pass\n"})
    ingester.index_project(root, max_files=1, max_symbols=1)
    assert set(check_index(engine)["partial_reasons"]) >= {"file_limit", "symbol_limit"}
    ingester.index_project(root, max_files=1, max_symbols=1)
    assert "symbol_limit" in check_index(engine)["partial_reasons"]
    ingester.index_project(root, max_files=10, max_symbols=10)
    assert check_index(engine)["status"] == "fresh"
    assert find_symbols(engine, "two")["total"] == 1


def test_watcher_batches_polyglot_edits_deletes_and_rebuilds_incoming_links(ingester, engine, tmp_path):
    root = project(tmp_path, {"a.py": "def helper():\n    return 1\n",
        "b.py": "from a import helper\n\ndef run():\n    return helper()\n", "web.ts": "function oldName() { return 1; }"})
    ingester.index_project(root)
    watcher = ReactiveWatcher(ingester, debounce=0)
    watcher.poll_once()
    (root / "a.py").write_text("\n\ndef helper():\n    return 2\n", encoding="utf-8")
    (root / "web.ts").unlink()
    (root / "new.ts").write_text("function newName() { return 2; }", encoding="utf-8")
    events = watcher.poll_once()
    assert len(events) == 1 and events[0]["status"] == "refreshed"
    assert ("b.py", 3, "a.py", 3) in edges(engine, root)
    assert find_symbols(engine, "oldName")["total"] == 0
    assert find_symbols(engine, "newName")["total"] == 1
    assert check_index(engine)["status"] == "fresh"
    (root / "a.py").unlink()
    watcher.poll_once()
    assert not engine.all_links()
    assert check_index(DrosteConceptEngine(engine.db_path))["status"] == "fresh"


def test_references_are_root_scoped_bounded_and_cycle_safe(ingester, engine, tmp_path):
    root = project(tmp_path, {"a.py": "def a():\n    return b()\ndef b():\n    return c()\ndef c():\n    return a()\n"})
    ingester.index_project(root)
    result = references(engine, "a", direction="callers", depth=5)
    assert len(result["references"]) == 3
    assert references(engine, "a", direction="callers", depth=5, limit=1)["truncated"]
    other = tmp_path / "other"
    other.mkdir()
    (other / "a.py").write_text("def a(): pass\n", encoding="utf-8")
    ingester.index_project(other)
    assert not references(engine, "a")["references"]
    assert len(references(engine, "a", root=root)["references"]) == 1
    ingester.index_project(root, reset=True)
    assert find_symbols(engine, "a", root=other)["total"] == 1
    assert check_index(engine, other)["status"] == "fresh"


@pytest.mark.parametrize("name,source", [
    ("A.java", "class A { int helper() { return 1; } }"),
    ("A.cs", "class A { int Helper() { return 1; } }"),
    ("a.rb", "def helper\n  1\nend\n"),
    ("a.php", "<?php function helper() { return 1; } ?>"),
])
def test_advertised_language_files_are_actually_collected(ingester, engine, tmp_path, name, source):
    root = project(tmp_path, {name: source})
    assert ingester.index_project(root)["stats"]["symbol_count"] > 0


def test_changing_embedding_models_cannot_reuse_vectors_from_another_space(ingester, engine, tmp_path):
    class Projector:
        model_error = None
        def __init__(self, model, marker):
            self.identity, self.marker = model, marker

        def embed_texts(self, texts):
            return [[self.marker] * 384 for text in texts]

    root = project(tmp_path, {"a.py": "def helper():\n    return 1\n"})
    engine.projector = Projector("first-model", 1.0)
    ingester.index_project(root)
    engine.projector = Projector("second-model", 2.0)
    result = ingester.index_project(root)
    assert result["reused_files"] == 0
    assert engine.index_state(root)["embedding_model"] == "second-model"
    assert all(n.embedding[0] == 2.0 for n in engine.all_nodes() if n.embedding)


def test_failed_watcher_refresh_retries_batch(ingester, engine, tmp_path, monkeypatch):
    root = project(tmp_path, {"a.py": "def helper(): pass\n"})
    ingester.index_project(root)
    watcher = ReactiveWatcher(ingester, debounce=0)
    watcher.poll_once()
    (root / "a.py").write_text("def changed(): pass\n", encoding="utf-8")
    actual = ingester.index_project
    monkeypatch.setattr(ingester, "index_project", lambda *a, **kw: (_ for _ in ()).throw(OSError("temporary failure")))
    assert watcher.poll_once()[0]["status"] == "error"
    monkeypatch.setattr(ingester, "index_project", actual)
    assert watcher.poll_once()[0]["status"] == "refreshed"
    assert find_symbols(engine, "changed")["total"] == 1


def test_ignore_rules_nested_negations_and_database_exclusion(ingester, engine, tmp_path):
    root = project(tmp_path, {".gitignore": "private/\n*.json\n", ".drosteignore": "extra.py\n",
        "private/secret.py": "def secret(): pass\n", "data.json": "{}",
        "extra.py": "def extra(): pass\n", "app/.gitignore": "*.py\n!keep.py\n",
        "app/keep.py": "def keep(): pass\n", "app/skip.py": "def skip(): pass\n"})
    ingester.index_project(root)
    assert find_symbols(engine, "keep")["total"] == 1
    assert find_symbols(engine, "secret")["total"] == 0
    assert find_symbols(engine, "skip")["total"] == 0
    assert find_symbols(engine, "extra")["total"] == 0
    assert check_index(engine)["status"] == "fresh"
    (root / ".drosteignore").write_text("", encoding="utf-8")
    assert check_index(engine)["added"] == ["extra.py"]
    (root / ".gitignore").write_text("private/\n", encoding="utf-8")
    local_engine = DrosteConceptEngine(root / "custom-state.json")
    force_hash_backend(local_engine)
    DrosteProjectIngester(local_engine).index_project(root)
    assert "custom-state.json" not in local_engine.index_state(root)["files"]


def test_static_method_receivers_are_not_treated_as_self(ingester, engine, tmp_path):
    root = project(tmp_path, {"a.py": "class A:\n    @staticmethod\n    def run(self):\n        return self.helper()\n    def helper(self): pass\n"})
    ingester.index_project(root)
    assert edges(engine, root) == set()


def test_disappeared_root_is_reported_as_stale(ingester, engine, tmp_path):
    root = project(tmp_path, {"a.py": "def helper(): pass\n"})
    ingester.index_project(root)
    (root / "a.py").unlink()
    root.rmdir()
    assert check_index(engine)["status"] == "stale"


@pytest.mark.parametrize("budget", [100, 500, 800, 1500, 6000])
def test_context_effective_character_limit_includes_separators(ingester, sample_project, budget):
    ingester.index_project(sample_project)
    result = ingester.get_context("hub_core", budget=budget)
    assert result["budget_unit"] == "characters"
    assert result["used"] == len(result["compiled_context"]) <= result["budget"]
    assert result["compiled_context"].strip()


def test_import_prefers_implementation_over_same_module_stub(ingester, engine, tmp_path):
    root = project(tmp_path, {"src/pkg/helper.py": "def helper():\n    return 1\n",
        "src/pkg/helper.pyi": "def helper() -> int: ...\n",
        "main.py": "from pkg.helper import helper\n\ndef run():\n    return helper()\n"})
    ingester.index_project(root)
    assert edges(engine, root) == {("main.py", 3, "src/pkg/helper.py", 1)}
