"""Build the public, precomputed token-zoom demo from pinned Requests source.

No model answers or Requests code are executed. The output contains public
source snippets; this generator refuses other source trees by checksum.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.droste_engine import DrosteConceptEngine
from core.exploration import explore, tokenizer


COMMIT = "b25c87d7cb8d6a18a37fa12442b5f883f9e41741"
ADAPTERS_HASH = "f275f5d7781b6f5db7694b71d844e400dffa22c617f8cd9f4682c696e4c47119"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    if hashlib.sha256((root / "src/requests/adapters.py").read_bytes()).hexdigest() != ADAPTERS_HASH:
        parser.error("Only the pinned public Requests tree is supported")
    engine = DrosteConceptEngine(args.db)
    state = engine.index_state(root)
    if not state:
        parser.error("Index the pinned tree first")
    # Verify every indexed source against the pinned archive, not just adapters.
    import io
    import urllib.request
    import zipfile
    with urllib.request.urlopen(f"https://codeload.github.com/psf/requests/zip/{COMMIT}", timeout=60) as response:
        archive = zipfile.ZipFile(io.BytesIO(response.read()))
    prefix = archive.namelist()[0]
    for relative, snapshot in state["files"].items():
        expected = archive.read(prefix + relative)
        actual = (root / relative).read_bytes()
        if actual != expected or hashlib.sha256(actual).hexdigest() != snapshot["sha256"]:
            parser.error("Source differs from the public pinned archive: " + relative)
    enc = tokenizer()
    nodes = {n.id: n for n in engine.all_nodes() if engine.normalize_root(n.index_root) == str(root)}
    links = [l for l in engine.all_links() if l.from_node in nodes and l.to_node in nodes]
    report = {"version": "1.3.0", "repository": "psf/requests", "commit": COMMIT,
              "encoding": "o200k_base", "budgets": [256, 512, 1000, 1500, 2500],
              "scope": "Precomputed real Droste responses. Token counts cover the complete returned text, not model wrappers or billing. Whole-file comparisons are size comparisons, not task-quality benchmarks.",
              "cases": []}
    for name, focus, path in [("Timeouts", "HTTPAdapter.send", "src/requests/adapters.py"),
                              ("Redirect credentials", "SessionRedirectMixin.rebuild_auth", "src/requests/sessions.py"),
                              ("Environment proxies", "should_bypass_proxies", "src/requests/utils.py")]:
        case = {"name": name, "focus": focus, "path": path,
                "whole_file_tokens": len(enc.encode_ordinary((root / path).read_text(encoding="utf-8"))),
                "packets": {}}
        for level in ("map", "structure", "source"):
            for budget in report["budgets"]:
                text = explore(engine, focus=focus, root=str(root), tokens=budget, level=level)
                packet = json.loads(text)
                assert packet["used"] == len(enc.encode_ordinary(text)) <= budget
                assert packet["status"] == "ok", packet
                assert "C:\\" not in text and str(root) not in text
                case["packets"][f"{level}:{budget}"] = text
        detailed = json.loads(case["packets"]["source:2500"])
        detail = detailed["items"][0]
        assert detail["level"] == "source"
        case["symbol_tokens"] = len(enc.encode_ordinary(detail["text"]))
        seen = [i["handle"] for i in detailed["items"]]
        replay = explore(engine, focus=focus, root=str(root), tokens=2500, level="source", seen=seen)
        assert not any(i["id"] == detailed["focus"] for i in json.loads(replay)["items"])
        case["repeat_packet"] = replay
        first_arguments = {"focus": focus, "level": "source", "tokens": 2500}
        case["first_argument_tokens"] = len(enc.encode_ordinary(json.dumps(first_arguments, separators=(",", ":"))))
        case["repeat_argument_tokens"] = len(enc.encode_ordinary(json.dumps({**first_arguments, "seen": seen}, separators=(",", ":"))))
        origin = detailed["focus"]
        nearby = {origin}
        for l in links:
            if l.from_node == origin or l.to_node == origin:
                nearby.update((l.from_node, l.to_node))
        case["nodes"] = [{"id": n.id, "name": n.title.split(": ", 1)[-1],
                           "path": Path(n.source_path).relative_to(root).as_posix(),
                           "lines": [n.line_start, n.line_end]} for node_id, n in nodes.items() if node_id in nearby]
        case["edges"] = [[l.from_node, l.to_node] for l in links if l.from_node in nearby and l.to_node in nearby]
        report["cases"].append(case)
        print(name, "full file", case["whole_file_tokens"], "source response", detailed["used"], "repeat", json.loads(replay)["used"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    # Include the unmodified upstream license with this public source demo.
    (args.output.parent / "requests-LICENSE.txt").write_bytes(archive.read(prefix + "LICENSE"))
    (args.output.parent / "requests-NOTICE.txt").write_bytes(archive.read(prefix + "NOTICE"))


if __name__ == "__main__":
    main()
