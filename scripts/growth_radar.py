"""Read-only public discovery checks for Droste.

This script is intentionally non-promotional. It does not post anywhere, send
messages, or create issues. It prints the public state that matters for launch:
GitHub, PyPI and MCP Registry visibility.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request


REPO_API = "https://api.github.com/repos/lorismascio17/droste-memory"
PYPI_API = "https://pypi.org/pypi/droste-memory/json"
MCP_REGISTRY_API = (
    "https://registry.modelcontextprotocol.io/v0.1/servers?"
    + urllib.parse.urlencode({"search": "io.github.lorismascio17/droste-memory"})
)


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "droste-growth-radar"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> None:
    print("Droste growth radar")
    print("===================")

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
        print(f"PyPI latest: {pypi.get('info', {}).get('version')}")
    except Exception as exc:
        print(f"PyPI check failed: {exc}")

    try:
        registry = fetch_json(MCP_REGISTRY_API)
        servers = registry.get("servers") or []
        found = any(
            server.get("name") == "io.github.lorismascio17/droste-memory"
            for server in servers
        )
        print(f"MCP Registry visible: {'yes' if found else 'no'}")
        if not found:
            print("Action: publish server.json after the matching PyPI release is live.")
    except Exception as exc:
        print(f"MCP Registry check failed: {exc}")


if __name__ == "__main__":
    main()
