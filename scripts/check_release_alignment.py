"""Check whether local release metadata matches public PyPI.

Used by the MCP Registry publishing workflow so scheduled runs do not publish a
server.json version before the matching PyPI package is live.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import sys
import urllib.request


ROOT = pathlib.Path(__file__).resolve().parents[1]
PYPI_API = "https://pypi.org/pypi/droste-memory/json"


def read_pyproject_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        raise RuntimeError("Could not find project version in pyproject.toml")
    return match.group(1)


def read_server_version() -> str:
    data = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
    server_version = data.get("version")
    package_versions = {
        package.get("version")
        for package in data.get("packages", [])
        if package.get("identifier") == "droste-memory"
    }
    versions = {server_version, *package_versions}
    if len(versions) != 1 or not server_version:
        raise RuntimeError(f"server.json version mismatch: {sorted(versions)}")
    return str(server_version)


def read_pypi_version() -> str:
    request = urllib.request.Request(PYPI_API, headers={"User-Agent": "droste-release-align"})
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return str(payload["info"]["version"])


def write_github_output(aligned: bool) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with open(output_path, "a", encoding="utf-8") as handle:
        handle.write(f"aligned={'true' if aligned else 'false'}\n")


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    github_output = "--github-output" in argv

    pyproject_version = read_pyproject_version()
    server_version = read_server_version()
    pypi_version = read_pypi_version()

    print(f"pyproject.toml: {pyproject_version}")
    print(f"server.json: {server_version}")
    print(f"PyPI: {pypi_version}")

    aligned = pyproject_version == server_version == pypi_version
    if github_output:
        write_github_output(aligned)

    if pyproject_version != server_version:
        print("Release mismatch: pyproject.toml and server.json differ.", file=sys.stderr)
        return 0 if github_output else 1
    if pypi_version != pyproject_version:
        print(
            "Release mismatch: upload the matching PyPI package before publishing MCP Registry.",
            file=sys.stderr,
        )
        print("MCP Registry publish skipped.")
        return 0 if github_output else 1

    print("Release metadata aligned.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
