"""Read-only public discovery checks for Droste.

This script is intentionally non-promotional. It does not post anywhere, send
messages, or create issues. It prints the public state that matters for launch:
GitHub, PyPI and MCP Registry visibility.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
import pathlib
import re
import urllib.parse
import urllib.request


ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO_API = "https://api.github.com/repos/lorismascio17/droste-memory"
PYPI_API = "https://pypi.org/pypi/droste-memory/json"
MCP_REGISTRY_API = "https://registry.modelcontextprotocol.io/v0.1/servers/io.github.lorismascio17%2Fdroste-memory/versions/latest"
MCP_SERVER_NAME = "io.github.lorismascio17/droste-memory"


def read_pyproject_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return match.group(1) if match else "<unknown>"


def read_server_version() -> str:
    data = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
    package_versions = [
        package.get("version")
        for package in data.get("packages", [])
        if package.get("identifier") == "droste-memory"
    ]
    if package_versions and package_versions[0] != data.get("version"):
        return f"{data.get('version')} (package mismatch: {package_versions[0]})"
    return str(data.get("version", "<unknown>"))


def dist_artifacts(version: str) -> list[str]:
    dist = ROOT / "dist"
    if not dist.exists():
        return []
    return sorted(path.name for path in dist.glob(f"droste_memory-{version}*"))


def expected_dist_artifacts(version: str) -> set[str]:
    return {
        f"droste_memory-{version}-py3-none-any.whl",
        f"droste_memory-{version}.tar.gz",
    }


def fetch_json(url: str) -> dict:
    headers = {"User-Agent": "droste-growth-radar"}
    if url.startswith("https://api.github.com/") and os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def registry_entries(payload: dict) -> list[dict]:
    entries = []
    for item in ([payload] if "server" in payload else payload.get("servers") or []):
        server = item.get("server") if isinstance(item, dict) else None
        if not isinstance(server, dict):
            server = item if isinstance(item, dict) else {}
        if server.get("name") == MCP_SERVER_NAME:
            entries.append({"server": server, "meta": item.get("_meta", {})})
    return entries


def latest_registry_version(entries: list[dict]) -> str | None:
    if not entries:
        return None
    for entry in entries:
        official = entry.get("meta", {}).get("io.modelcontextprotocol.registry/official", {})
        if official.get("isLatest") is True:
            return str(entry["server"].get("version"))
    return str(entries[-1]["server"].get("version"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=pathlib.Path, help="Save public observations as JSON")
    args = parser.parse_args()
    report = {"observed_at": datetime.now(timezone.utc).isoformat(), "errors": {}}
    failures = []
    print("Droste growth radar")
    print("===================")

    local_version = read_pyproject_version()
    server_version = read_server_version()
    print(f"Local pyproject version: {local_version}")
    print(f"Local server.json version: {server_version}")
    artifacts = dist_artifacts(local_version)
    print(f"Local dist artifacts: {', '.join(artifacts) if artifacts else '<none>'}")

    pypi_version = None
    try:
        repo = fetch_json(REPO_API)
        report["github"] = {key: repo.get(key) for key in ("stargazers_count", "forks_count", "open_issues_count", "pushed_at", "topics")}
        topics = ", ".join(repo.get("topics") or [])
        print(f"GitHub stars: {repo.get('stargazers_count')}")
        print(f"GitHub topics: {topics or '<none>'}")
        print(f"GitHub pushed_at: {repo.get('pushed_at')}")
    except Exception as exc:
        print(f"GitHub check failed: {exc}")
        failures.append("github")
        report["errors"]["github"] = str(exc)

    try:
        pypi = fetch_json(PYPI_API)
        pypi_version = pypi.get("info", {}).get("version")
        report["pypi_version"] = pypi_version
        print(f"PyPI latest: {pypi_version}")
    except Exception as exc:
        print(f"PyPI check failed: {exc}")
        failures.append("pypi")
        report["errors"]["pypi"] = str(exc)

    registry_visible = None
    registry_version = None
    try:
        registry = fetch_json(MCP_REGISTRY_API)
        entries = registry_entries(registry)
        registry_visible = bool(entries)
        registry_version = latest_registry_version(entries)
        report["mcp_registry_version"] = registry_version
        if registry_visible:
            print(f"MCP Registry visible: yes (latest {registry_version})")
        else:
            print("MCP Registry visible: no")
    except Exception as exc:
        print(f"MCP Registry check failed: {exc}")
        failures.append("registry")
        report["errors"]["registry"] = str(exc)

    try:
        downloads = fetch_json("https://pypistats.org/api/packages/droste-memory/recent")
        report["pypi_downloads"] = downloads.get("data", {})
        print(f"PyPI downloads (not active users): {report['pypi_downloads']}")
    except Exception as exc:
        report["errors"]["pypistats"] = str(exc)
        print(f"Optional PyPI download statistics unavailable: {exc}")

    print()
    print("Next actions")
    print("------------")
    if local_version != server_version:
        print("- Fix local version mismatch between pyproject.toml and server.json.")
    elif pypi_version != local_version:
        missing = sorted(expected_dist_artifacts(local_version) - set(artifacts))
        if missing:
            print(
                f"- Build PyPI {local_version} artifacts first: "
                "python -m build --no-isolation"
            )
            print(f"  Missing: {', '.join(missing)}")
        else:
            print(
                f"- Upload PyPI {local_version}: "
                f"python -m twine upload dist/droste_memory-{local_version}*"
            )
    elif registry_visible is False or registry_version != local_version:
        print("- Run GitHub Actions workflow: Publish MCP Registry.")
    elif registry_visible is True:
        print("- Start directory submissions from docs/SUBMISSION_PACK.md.")
    else:
        print("- Re-run after network/API checks are available.")

    report["local_version"] = local_version
    report["server_version"] = server_version
    report["aligned"] = local_version == server_version == pypi_version == registry_version
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 1 if failures or local_version != server_version else 0


if __name__ == "__main__":
    raise SystemExit(main())
