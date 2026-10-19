"""Check actual parser output: import success alone cannot detect API drift."""
import pytest
from core import treesitter_extract


@pytest.mark.parametrize("extension,source", [
    (".ts", "function helper(x: number) { return x * 2; }\nfunction calculate(x: number) { return helper(x); }"),
    (".js", "function helper(x) { return x * 2; }\nfunction calculate(x) { return helper(x); }"),
    (".go", "package main\nfunc helper(x int) int { return x * 2 }\nfunc calculate(x int) int { return helper(x) }"),
    (".rs", "fn helper(x: i32) -> i32 { x * 2 }\nfn calculate(x: i32) -> i32 { helper(x) }"),
    (".c", "int helper(int x) { return x * 2; }\nint calculate(int x) { return helper(x); }"),
])
def test_parser_produces_definitions_and_references(extension, source):
    symbols = treesitter_extract.extract_symbols(source, extension)
    assert symbols, f"Parser silently fell back for {extension}"
    by_name = {s["name"]: s for s in symbols}
    assert {"helper", "calculate"} <= by_name.keys()
    assert "helper" in by_name["calculate"]["references"]
    assert all(s["ts_based"] for s in symbols)


def test_typescript_index_has_syntax_edges(ingester, engine, tmp_path):
    project = tmp_path / "typescript-project"
    project.mkdir()
    (project / "service.ts").write_text(
        "function helper(x: number) { return x * 2; }\n"
        "function calculate(x: number) { return helper(x); }\n", encoding="utf-8")
    ingester.index_project(project)
    names = {node.id: node.title for node in engine.all_nodes()}
    assert any(
        link.type == "syntax_dependency"
        and names[link.from_node].endswith("calculate")
        and names[link.to_node].endswith("helper")
        for link in engine.all_links()
    )
