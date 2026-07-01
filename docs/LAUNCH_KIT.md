# Droste Launch Kit

This file is a practical launch checklist and copy bank for public posts. Keep
the tone technical: ask for feedback, not stars.

## Current Public State

- GitHub: https://github.com/lorismascio17/droste-memory
- PyPI: https://pypi.org/project/droste-memory/
- Install: `python -m pip install --upgrade droste-memory`
- Latest version: `1.1.3`
- Main command: `droste`
- MCP command: `droste mcp`

## GitHub Release Notes: v1.1.3

Title:

```text
Droste v1.1.3 - MCP Registry and agent skills
```

Body:

```text
Droste v1.1.3 turns the project into a more discoverable MCP and agent-skill package.

Highlights:
- Added MCP Registry ownership metadata in the README/PyPI description.
- Added server.json for official MCP Registry publishing.
- Added a manual GitHub Actions workflow to publish Droste to the MCP Registry.
- Added a scheduled Growth Radar workflow to monitor GitHub, PyPI and MCP Registry visibility.
- Added public Codex and Claude skill templates so agents can learn Droste's safe indexing and context workflow.

Install or upgrade:

python -m pip install --upgrade droste-memory

MCP server:

droste mcp

Server name:

io.github.lorismascio17/droste-memory
```

## GitHub Release Notes: v1.1.1

Title:

```text
Droste v1.1.1 - Packaging and discovery hardening
```

Body:

```text
Droste v1.1.1 is a small but important public-release hardening pass.

Highlights:
- PyPI package metadata now includes keywords, classifiers, project URLs, and MIT license metadata.
- README now shows PyPI, Python version, CI, and license badges.
- Generated visualizer JSON files are excluded from source distributions, preventing local project graph data from being packaged accidentally.
- The public self-demo graph remains included as visualizer/demo_graph.json.

Install or upgrade:

python -m pip install --upgrade droste-memory

MCP server:

droste mcp

This builds on v1.1.0, which added project-root isolation for MCP context, Windows UTF-8 output safety, and query-aware ranking for runtime/test/doc retrieval.
```

## Show HN Draft

Title:

```text
Show HN: Droste, a local code-memory MCP server for AI coding agents
```

Post:

```text
I built Droste, a local code-memory MCP server for AI coding agents.

Instead of relying only on vector search or blind file reads, Droste indexes a repository into a hybrid structural + semantic graph: folders, files, symbols, caller/callee edges, and local embeddings.

The goal is to give coding agents causal context: not just files that look similar to a query, but the surrounding code relationships that explain what will break if something changes.

It includes:
- Local-first MCP server: droste mcp
- CLI: droste index, status, context, zoom, view
- Tree-sitter symbol extraction
- Sharded local storage
- Project-root isolation for MCP context
- Query-aware ranking for runtime vs tests/docs
- Fractal code visualizer with graph edges

Install:
python -m pip install --upgrade droste-memory

GitHub:
https://github.com/lorismascio17/droste-memory

I would appreciate technical feedback from people building AI coding agents, local-first developer tools, or code intelligence systems.
```

## Reddit Draft

Use only in communities where open-source developer tools are allowed. Read each
subreddit rule first.

Title:

```text
I built a local MCP code-memory engine for AI coding agents
```

Post:

```text
I have been working on Droste, an open-source local code-memory engine for AI coding agents.

The idea is simple: coding agents should not rely only on vector search or repeated blind file reads. Droste indexes a repo into a structural + semantic graph and exposes it through MCP, so an agent can ask for causal context around a feature, symbol, or bug.

It is local-first, Python-based, and installable from PyPI:

python -m pip install --upgrade droste-memory

Then:

droste index .
droste context "checkout flow" --budget 1500
droste mcp

GitHub:
https://github.com/lorismascio17/droste-memory

I am looking for technical feedback, especially around retrieval quality, MCP ergonomics, and whether the visual graph is useful in real projects.
```

## Short Social Post

```text
I released Droste v1.1.1.

Droste is a local MCP code-memory engine for AI coding agents: structural graph + semantic search, sharded local storage, project isolation, and fractal code visualization.

Install:
python -m pip install --upgrade droste-memory

GitHub:
https://github.com/lorismascio17/droste-memory
```

## Suggested GitHub Topics

```text
mcp
ai-agents
rag
code-search
semantic-search
tree-sitter
developer-tools
local-first
code-intelligence
python
```

## Launch Order

1. Create the GitHub Release for `v1.1.1` using the release notes above.
2. Confirm PyPI shows version `1.1.1`.
3. Post Show HN first.
4. Wait and respond to comments for at least a few hours.
5. Post to one targeted Reddit community only after checking the rules.
6. Later, submit small PRs to relevant awesome lists.

## Rules

- Do not ask people to star the repo.
- Do not mass-post the same copy everywhere.
- Do not overclaim benchmark results; link to the README and let the numbers
  speak.
- Ask for specific feedback: MCP config, retrieval quality, ranking, visualizer,
  installation issues.
