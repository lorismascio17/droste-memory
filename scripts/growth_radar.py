"""Read-only public discovery checks for Droste.

This script is intentionally non-promotional. It does not post anywhere, send
messages, or create issues. It prints the public state that matters for launch:
GitHub, PyPI and MCP Registry visibility.
"""

from __future__ import annotations

import json
import pathlib
import re
import urllib.parse
import urllib.request


ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO_API = "https://api.github.com/repos/lorismascio17/droste-memory"
PYPI_API = "https://pypi.org/pypi/droste-memory/json"
MCP_REGISTRY_API = (
    "https://registry.modelcontextprotocol.io/v0.1/servers?"
    + urllib.parse.urlencode({"search": "io.github.lorismascio17/droste-memory"})
)


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


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "droste-growth-radar"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> None:
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
        topics = ", ".join(repo.get("topics") or [])
        print(f"GitHub stars: {repo.get('stargazers_count')}")
        print(f"GitHub topics: {topics or '<none>'}")
        print(f"GitHub pushed_at: {repo.get('pushed_at')}")
    except Exception as exc:
        print(f"GitHub check failed: {exc}")

    try:
        pypi = fetch_json(PYPI_API)
        pypi_version = pypi.get("info", {}).get("version")
        print(f"PyPI latest: {pypi_version}")
    except Exception as exc:
        print(f"PyPI check failed: {exc}")

    registry_visible = None
    try:
        registry = fetch_json(MCP_REGISTRY_API)
        servers = registry.get("servers") or []
        registry_visible = any(
            server.get("name") == "io.github.lorismascio17/droste-memory"
            for server in servers
        )
        print(f"MCP Registry visible: {'yes' if registry_visible else 'no'}")
    except Exception as exc:
        print(f"MCP Registry check failed: {exc}")

    print()
    print("Next actions")
    print("------------")
    if local_version != server_version:
        print("- Fix local version mismatch between pyproject.toml and server.json.")
    elif pypi_version != local_version:
        print(f"- Upload PyPI {local_version}: python -m twine upload dist/*")
    elif registry_visible is False:
        print("- Run GitHub Actions workflow: Publish MCP Registry.")
    elif registry_visible is True:
        print("- Start directory submissions from docs/SUBMISSION_PACK.md.")
    else:
        print("- Re-run after network/API checks are available.")


if __name__ == "__main__":
    main()
