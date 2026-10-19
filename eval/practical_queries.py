"""Record practical Requests investigations through the real CLI.

Downloads pinned source for parsing only, never imports or executes Requests.
Outputs observations, not an automatically computed usefulness score. Semantic
queries use the configured embedding backend (the default may download a model).
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import zipfile


COMMIT = "b25c87d7cb8d6a18a37fa12442b5f883f9e41741"
ARCHIVE_SHA256 = "1cf78dc00bd661c551eacf922d8bb0a4a2aa95435e8333366b77a6ccee188908"
CONTEXTS = [
    ("redirect-en", "Where is the Authorization header removed when a redirect changes the host?"),
    ("timeout-en", "Where is the connect and read timeout tuple handled before sending an HTTP request?"),
    ("proxy-en", "How does NO_PROXY bypass environment proxy configuration?"),
    ("redirect-it", "Dove vengono rimosse le credenziali quando un redirect cambia host?"),
    ("redirect-symbol", "SessionRedirectMixin.rebuild_auth"),
    ("timeout-symbol", "HTTPAdapter.send timeout"),
    ("proxy-symbol", "should_bypass_proxies"),
]
INSPECTIONS = [
    ("freshness", ["check", "--json"]),
    ("auth-find", ["find", "rebuild_auth", "--json"]),
    ("auth-callers", ["references", "SessionRedirectMixin.rebuild_auth", "--json"]),
    ("strip-callers", ["references", "SessionRedirectMixin.should_strip_auth", "--json"]),
    ("timeout-ambiguous", ["references", "send", "--json"]),
    ("timeout-callees", ["references", "HTTPAdapter.send", "--direction", "callees", "--json"]),
    ("proxy-callers", ["references", "should_bypass_proxies", "--depth", "2", "--json"]),
    ("request-trace", ["references", "get", "--file", "src/requests/api.py", "--direction", "callees", "--depth", "5", "--json"]),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", required=True, type=Path, help="Dedicated scratch directory; contains downloaded source and index.")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--checkout", action="store_true", help="Use this source checkout instead of the isolated installed package.")
    args = parser.parse_args()
    work = args.work_dir.resolve()
    work.mkdir(parents=True, exist_ok=True)
    root = work / "requests"
    if root.exists():
        parser.error("Choose a new scratch directory: requests/ already exists.")
    url = f"https://codeload.github.com/psf/requests/zip/{COMMIT}"
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "droste-practical-review"}), timeout=60) as response:
        data = response.read()
    digest = hashlib.sha256(data).hexdigest()
    if digest != ARCHIVE_SHA256:
        raise RuntimeError("Pinned archive checksum changed; inspect the upstream source before proceeding.")
    root.mkdir()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for entry in archive.infolist():
            parts = Path(entry.filename).parts[1:]
            if entry.is_dir() or not parts:
                continue
            target = root.joinpath(*parts).resolve()
            if not target.is_relative_to(root):
                raise ValueError("Unsafe archive member")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(entry))

    command = [sys.executable, *([] if args.checkout else ["-I"]), "-m", "core.droste_cli", "--db", str(work / "index.json")]

    def run(label, arguments):
        start = time.perf_counter()
        process = subprocess.run([*command, *arguments], cwd=Path(__file__).resolve().parents[1],
                                 capture_output=True, text=True, encoding="utf-8", timeout=600)
        if process.returncode not in (0, 1, 2):
            raise RuntimeError(process.stderr or process.stdout)
        result = {"id": label, "arguments": arguments, "exit_code": process.returncode,
                  "seconds_including_startup": round(time.perf_counter() - start, 3),
                  "stdout_characters": len(process.stdout), "result": json.loads(process.stdout)}
        print(f"{label}: {result['seconds_including_startup']}s", flush=True)
        return result

    report = {"repository": "psf/requests", "commit": COMMIT, "archive_sha256": digest,
              "interface": "checkout CLI" if args.checkout else "isolated installed CLI",
              "budget_characters": 6000,
              "index": run("index", ["index", str(root), "--json"]),
              "contexts": [run(label, ["context", query, "--budget", "6000", "--json"]) for label, query in CONTEXTS],
              "inspections": [run(label, arguments) for label, arguments in INSPECTIONS]}
    # Public corpus only; portable output must not disclose the operator's paths.
    def portable(value):
        if isinstance(value, str):
            return value.replace(str(work), "<work>")
        if isinstance(value, list):
            return [portable(item) for item in value]
        if isinstance(value, dict):
            return {key: portable(item) for key, item in value.items()}
        return value
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(portable(report), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
