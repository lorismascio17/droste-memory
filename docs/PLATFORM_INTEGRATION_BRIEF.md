# Droste Platform Integration Brief

This brief is for maintainers of AI coding platforms, MCP directories, agent
skill registries and developer-tool ecosystems.

## One-Line Summary

Droste is a local MCP code-memory engine that lets coding agents retrieve
causal repository context from a structural + semantic graph instead of relying
only on blind file reads or vector-similar chunks.

## Integration Ask

Consider one of these integration paths:

- List Droste as a recommended local MCP code-memory server.
- Add the Droste agent skill template to a curated skill/plugin directory.
- Use Droste as an example MCP server for repository-aware coding agents.
- Evaluate Droste as an optional local memory layer for code editing workflows.

## Why It Matters

Coding agents often need causal context: callers, callees, imports, symbols and
cross-language edges around a change. Plain semantic search can miss related
code that shares no keywords with the query. Droste indexes the repo into a
local graph and exposes context through MCP, CLI and skill templates.

Measured structural retrieval in the included eval reports:

- Droste neighbour-recall: 0.94
- Vector-RAG core neighbour-recall: 0.18
- Lexical core neighbour-recall: 0.42

The claim is narrow: Droste is strongest at structural and causal retrieval, not
as a universal semantic search replacement.

## User Install Path

```bash
python -m pip install --upgrade droste-memory
droste index .
droste mcp
```

MCP client config:

```json
{
  "mcpServers": {
    "droste": {
      "command": "droste",
      "args": ["mcp"]
    }
  }
}
```

For serious multi-repo use, configure an isolated DB per repository:

```json
{
  "mcpServers": {
    "droste": {
      "command": "droste",
      "args": [
        "--db",
        "/absolute/path/to/project/.droste/droste_memory_db.json",
        "mcp"
      ]
    }
  }
}
```

## Agent Skill Path

Droste ships reference skill templates for:

- Codex: `integrations/codex-skill/droste-code-memory/`
- Claude-compatible agents: `integrations/claude-skill/droste-code-memory/`

The skills teach an agent to:

- install/check Droste;
- index the current repository;
- use isolated DBs for multi-repo safety;
- call `droste_get_context` before broad file reads;
- inspect source files before making final claims.

## MCP Tools

Primary tools:

- `droste_index_project(path, reset=false)`
- `droste_get_context(query, budget=1500, root=null)`
- `droste_status(root=null)`

Supporting tools:

- `inject_concept`
- `move_camera_and_zoom`
- `get_space_status`

## Safety Model

- Local-first: no external API key is required for indexing or retrieval.
- Storage is local JSON sharding under `.droste/nodes/`.
- MCP root isolation avoids cross-repo context contamination.
- Windows stdout/stderr are UTF-8 guarded.
- Generated private graph exports are excluded from source distributions.
- Tests cover sharding, seqlock race protection, root isolation, ranking and
  token packing.

## Verification Commands

```bash
python -m pip install --upgrade droste-memory
droste --version
droste index .
droste status --json
droste context "authentication flow" --budget 1500
droste mcp
```

Repository checks:

```bash
python -m pytest tests/
python scripts/growth_radar.py
```

## Links

- GitHub: https://github.com/lorismascio17/droste-memory
- PyPI: https://pypi.org/project/droste-memory/
- MCP server name: `io.github.lorismascio17/droste-memory`

