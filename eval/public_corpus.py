"""Pinned public-source edge assertions, independent of Droste's own graph.

Downloads source for parsing only; never imports or executes downloaded code.
Labels are manually read call relationships, not generated from the index.
This small integration corpus is not a product or agent-task benchmark.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.droste_engine import DrosteConceptEngine
from core.droste_ingester import DrosteProjectIngester
from core.inspection import find_symbols, references


CORPUS = [
    {"repository": "psf/requests", "commit": "b25c87d7cb8d6a18a37fa12442b5f883f9e41741",
     "files": ["src/requests/api.py", "src/requests/sessions.py"],
     "positive": [("src/requests/api.py", name, "src/requests/api.py", "request")
                  for name in ("get", "options", "head", "post", "put", "patch", "delete")]
                  + [("src/requests/api.py", "request", "src/requests/sessions.py", "Session")],
     "known_missing": [("src/requests/api.py", "request", "src/requests/sessions.py", "Session.request")],
     "negative": [("src/requests/api.py", "get", "src/requests/sessions.py", "Session.request"),
                  ("src/requests/api.py", "post", "src/requests/api.py", "get")]},
    {"repository": "python/cpython", "commit": "0cc81280367df838c4b199f8f0378837165071c2",
     "files": ["Lib/urllib/parse.py"],
     "positive": [("Lib/urllib/parse.py", a, "Lib/urllib/parse.py", b) for a, b in [
         ("quote_plus", "quote"), ("unquote_plus", "unquote"), ("parse_qs", "parse_qsl"),
         ("urlparse", "urlsplit"), ("urlunparse", "urlunsplit"), ("urljoin", "urlparse"), ("urldefrag", "urlparse")]],
     "known_missing": [],
     "negative": [("Lib/urllib/parse.py", "quote_plus", "Lib/urllib/parse.py", "urlparse"),
                  ("Lib/urllib/parse.py", "urlparse", "Lib/urllib/parse.py", "parse_qs")]},
]


def evaluate(work: Path):
    os.environ["DROSTE_EMBEDDING_BACKEND"] = "hash"  # structural correctness only
    reports = []
    for entry in CORPUS:
        root = work / entry["repository"].replace("/", "-")
        sources = []
        for relative in entry["files"]:
            url = f"https://raw.githubusercontent.com/{entry['repository']}/{entry['commit']}/{relative}"
            request = urllib.request.Request(url, headers={"User-Agent": "droste-public-corpus"})
            with urllib.request.urlopen(request, timeout=30) as response:
                data = response.read()
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            sources.append({"path": relative, "url": url, "sha256": hashlib.sha256(data).hexdigest()})
        engine = DrosteConceptEngine(work / (root.name + ".json"))
        index = DrosteProjectIngester(engine).index_project(root, max_files=100, max_symbols=5000)
        assertions = []
        for category in ("positive", "known_missing", "negative"):
            for source_file, source, target_file, target in entry[category]:
                origin = [m for m in find_symbols(engine, source, file=source_file)["matches"] if m["qualified_name"] == source]
                destination = [m for m in find_symbols(engine, target, file=target_file)["matches"] if m["qualified_name"] == target]
                if len(origin) != 1 or len(destination) != 1:
                    raise RuntimeError(f"Corpus symbols must resolve uniquely: {source} -> {target}")
                result = references(engine, origin[0]["id"], direction="callees", limit=500)
                exists = any(r["to"]["id"] == destination[0]["id"] for r in result["references"])
                expected = category != "negative"
                assertions.append({"source_file": source_file, "source": source, "target_file": target_file,
                                   "target": target, "expected": expected, "observed": exists,
                                   "known_gap": category == "known_missing", "passed": exists == expected})
        reports.append({"repository": entry["repository"], "commit": entry["commit"], "sources": sources,
                        "index_partial": index["partial"], "assertions": assertions})
    cases = [case for report in reports for case in report["assertions"]]
    positives = [case for case in cases if case["expected"]]
    negatives = [case for case in cases if not case["expected"]]
    summary = {"assertions": len(cases), "passed": sum(c["passed"] for c in cases),
               "positive_edges_found": sum(c["observed"] for c in positives), "positive_edges": len(positives),
               "false_edges_on_negative_assertions": sum(c["observed"] for c in negatives),
               "negative_assertions": len(negatives),
               "unexpected_failures": sum(not c["passed"] and not c["known_gap"] for c in cases)}
    return {"scope": "Small manually labelled source-edge corpus; not exhaustive precision, semantic retrieval or agent-task performance.",
            "backend": "hash", "summary": summary, "repositories": reports}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--strict", action="store_true", help="Fail on known gaps as well as unexpected errors.")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="droste-public-") as directory:
        report = evaluate(Path(directory))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))
    return int(report["summary"]["unexpected_failures"] > 0 or
               (args.strict and report["summary"]["passed"] != report["summary"]["assertions"]))


if __name__ == "__main__":
    raise SystemExit(main())
