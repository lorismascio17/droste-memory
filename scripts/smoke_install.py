"""Exercise an installed wheel from outside the source tree, fully offline."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request


def main() -> None:
    import core
    if "site-packages" not in str(Path(core.__file__).resolve()):
        raise RuntimeError("Smoke test must use a wheel installed in site-packages, not the checkout")
    with tempfile.TemporaryDirectory(prefix="droste-smoke-") as directory:
        work = Path(directory)
        project = work / "project"
        project.mkdir()
        (project / "sample.py").write_text("def double(x):\n    return x * 2\n\ndef calculate(x):\n    return double(x)\n", encoding="utf-8")
        db = work / "state" / "custom.json"
        env = {**os.environ, "DROSTE_EMBEDDING_BACKEND": "hash", "DROSTE_DB": str(db)}
        command = [sys.executable, "-I", "-m", "core.droste_cli"]

        def run(*args):
            return subprocess.run([*command, *args], cwd=project, env=env, check=True, capture_output=True, text=True, encoding="utf-8", timeout=90).stdout

        run("index", str(project), "--reset")
        status = json.loads(run("status", "--json"))
        assert status["node_count"] >= 4, status
        context = json.loads(run("context", "calculate", "--budget", "1000", "--json"))
        assert "calculate" in json.dumps(context)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        with tempfile.TemporaryFile() as log:
            process = subprocess.Popen([*command, "view", "--no-open", "--port", str(port)], cwd=project, env=env, stdout=log, stderr=log)
            try:
                deadline = time.monotonic() + 45
                while True:
                    try:
                        with urllib.request.urlopen(f"http://127.0.0.1:{port}/graph.json", timeout=2) as response:
                            graph = json.load(response)
                        break
                    except OSError:
                        if process.poll() is not None or time.monotonic() > deadline:
                            log.seek(0)
                            raise RuntimeError(log.read().decode("utf-8", errors="replace"))
                        time.sleep(0.2)
                assert Path(graph["root"]) == project, graph["root"]
                assert graph["counts"]["symbol"] == 2
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/cockpit.html", timeout=5) as response:
                    assert response.status == 200
            finally:
                process.terminate()
                process.wait(timeout=15)

        async def mcp_check():
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
            parameters = StdioServerParameters(command=sys.executable, args=["-I", "-m", "core.droste_cli", "--db", str(db), "mcp"], env=env)
            async with stdio_client(parameters) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    assert "droste_get_context" in {tool.name for tool in tools.tools}
                    result = await session.call_tool("droste_status", {})
                    assert not result.isError
                    assert "node_count" in str(result)
        asyncio.run(asyncio.wait_for(mcp_check(), timeout=45))
    print("Installed wheel smoke passed: index, status, context, HTTP viewer, MCP handshake and tool call")


if __name__ == "__main__":
    main()
