"""Regressions at the installed-package, database and MCP boundaries."""
import argparse
import asyncio
import json
from pathlib import Path

from conftest import force_hash_backend
from core.droste_engine import DrosteConceptEngine, DrosteNode
from core.droste_ingester import DrosteProjectIngester


def test_databases_in_same_directory_do_not_overwrite_each_other(tmp_path):
    first = DrosteConceptEngine(tmp_path / "first.json")
    second = DrosteConceptEngine(tmp_path / "second.json")
    for engine, name in [(first, "first"), (second, "second")]:
        force_hash_backend(engine)
        engine.replace_indexed_nodes([
            DrosteNode(id=name, title=name, summary=name, detail_content="", source_path="shared.py", index_root=str(tmp_path))
        ], index_root=str(tmp_path), reset=True)
    second.reset_space()
    assert [n.id for n in DrosteConceptEngine(first.db_path).all_nodes()] == ["first"]
    assert DrosteConceptEngine(second.db_path).all_nodes() == []
    a, b = DrosteProjectIngester(first), DrosteProjectIngester(second)
    assert a._file_registry_path() != b._file_registry_path()
    assert a._embed_cache_path() != b._embed_cache_path()


def test_legacy_shards_migrate_without_deleting_originals(tmp_path):
    legacy = tmp_path / ".droste" / "nodes"
    legacy.mkdir(parents=True)
    node = DrosteNode(id="old", title="old", summary="old", detail_content="", source_path="a.py").to_dict()
    shard = legacy / "legacy.json"
    shard.write_text(json.dumps([node]), encoding="utf-8")
    db = tmp_path / "old.json"
    db.write_text(json.dumps({"storage": "sharded", "nodes": [], "links": []}), encoding="utf-8")
    engine = DrosteConceptEngine(db)
    assert [n.id for n in engine.all_nodes()] == ["old"]
    assert engine.ensure_sharded_storage()["status"] == "migrated"
    assert shard.exists()
    assert json.loads(db.read_text())["storage_layout"] == "namespaced-v1"
    assert [n.id for n in DrosteConceptEngine(db).all_nodes()] == ["old"]
    engine.reset_space()
    assert shard.exists()
    assert DrosteConceptEngine(db).all_nodes() == []


def test_view_uses_requested_database_and_temporary_assets(engine, ingester, sample_project, monkeypatch):
    import http.server
    from core import droste_cli
    ingester.index_project(sample_project)
    served = []

    class Server:
        server_port = 12345

        def __init__(self, address, handler):
            self.directory = Path(handler.keywords["directory"])
            served.append(self.directory)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def serve_forever(self):
            graph = json.loads((self.directory / "graph.json").read_text(encoding="utf-8"))
            assert Path(graph["root"]) == sample_project
            assert graph["counts"]["symbol"] > 0
            assert (self.directory / "cockpit.html").is_file()
            assert {p.name for p in self.directory.iterdir()} == {"cockpit.html", "graph.json", "status.json"}

    monkeypatch.setattr(http.server, "ThreadingHTTPServer", Server)
    args = argparse.Namespace(db=engine.db_path, root=str(sample_project), port=0, no_open=True)
    assert droste_cli.command_view(args) == 0
    assert served and not served[0].exists()


def test_mcp_registers_primary_tools(tmp_path):
    from core.droste_mcp import create_mcp_server
    server = create_mcp_server(tmp_path / "mcp.json")
    tools = asyncio.run(server.list_tools())
    assert {"droste_index_project", "droste_get_context", "droste_status"} <= {t.name for t in tools}


def test_default_database_respects_environment(tmp_path, monkeypatch):
    from core.storage_paths import default_db_path
    monkeypatch.setenv("DROSTE_DB", str(tmp_path / "custom.json"))
    assert default_db_path() == tmp_path / "custom.json"


def test_installed_default_does_not_write_to_package(tmp_path, monkeypatch):
    from core import storage_paths
    monkeypatch.delenv("DROSTE_DB", raising=False)
    monkeypatch.setattr(storage_paths, "__file__", str(tmp_path / "site-packages" / "core" / "storage_paths.py"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "user-data"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "user-data"))
    assert "site-packages" not in str(storage_paths.default_db_path())


def test_explicit_offline_backend_never_loads_model(monkeypatch):
    from core.embedding_projector import EmbeddingProjector
    monkeypatch.setenv("DROSTE_EMBEDDING_BACKEND", "hash")
    projector = EmbeddingProjector()
    assert projector.backend == "hash"
    assert len(projector.embed_text("offline code")) == 384
    assert projector._fastembed is None
