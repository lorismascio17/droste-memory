# Droste Submission Pack

Use this pack only after public package and registry state are aligned:

```bash
python scripts/growth_radar.py
```

Required before submitting anywhere:

- `Local pyproject version` equals `PyPI latest`.
- `Local server.json version` equals `PyPI latest`.
- `MCP Registry visible: yes`.

Do not mass-submit. Submit to one durable directory/list at a time, then wait for
review or indexing.

## 1. Official MCP Registry

Status: required first.

Action:

1. Publish the matching PyPI version.
2. Run the GitHub Actions workflow:

```text
https://github.com/lorismascio17/droste-memory/actions/workflows/publish-mcp-registry.yml
```

Expected server name:

```text
io.github.lorismascio17/droste-memory
```

## 2. Glama MCP Directory

Glama lists open-source MCP servers and exposes an `Add Server` flow from:

```text
https://glama.ai/mcp/servers
```

Suggested fields:

```text
Name:
Droste

Repository:
https://github.com/lorismascio17/droste-memory

Package:
droste-memory

Server name:
io.github.lorismascio17/droste-memory

Install:
python -m pip install --upgrade droste-memory

Run:
droste mcp

Categories:
Developer Tools, Code Analysis, Knowledge & Memory, RAG Systems, Local, Python

Description:
Local MCP code-memory engine for AI coding agents. Droste indexes a repository into a structural + semantic graph and returns causal context through CLI and MCP, with sharded local storage, project-root isolation, and Codex/Claude skill templates.
```

## 3. awesome-mcp-servers PR

Repository:

```text
https://github.com/punkpeye/awesome-mcp-servers
```

Best target section:

```text
Developer Tools
```

Acceptable alternate sections:

```text
Knowledge & Memory
Coding Agents
Search & Data Extraction
```

Suggested README entry:

```markdown
- [lorismascio17/droste-memory](https://github.com/lorismascio17/droste-memory) 🐍 🏠 🍎 🪟 🐧 - Local MCP code-memory engine for AI coding agents. Builds a structural + semantic repository graph and returns causal context through CLI and MCP, with sharded local storage and project-root isolation.
```

Pull request title:

```text
Add Droste code-memory MCP server
```

Pull request body:

```text
Adds Droste, an open-source local MCP code-memory engine for AI coding agents.

Droste indexes a repository into a structural + semantic graph and exposes causal context retrieval through CLI and MCP. It is distributed on PyPI as `droste-memory`, includes an official `server.json`, and provides Codex/Claude skill templates for agent-side usage.
```

## 4. Dev.to Follow-up Comment

Add a short update below the existing article after `1.1.4` is public:

```text
Update: Droste now includes server.json for MCP Registry publishing and ships Codex + Claude skill templates under integrations/. The goal is to make it usable both as an MCP server and as an agent-side workflow skill.
```

## 5. Direct Outreach

Use only for targeted technical feedback, not promotion blasts.

```text
Hi, I built Droste, a local MCP code-memory engine for AI coding agents.

It combines structural code graph retrieval with semantic search, so agents can retrieve caller/callee and cross-language context instead of only vector-similar chunks.

It now has a server.json for MCP Registry publishing and includes Codex/Claude skill templates.

I am not asking for promotion; I would value technical feedback from someone working in MCP, code intelligence or agent tooling.

GitHub:
https://github.com/lorismascio17/droste-memory
```

## 6. Anti-spam Rules

- Do not ask for stars.
- Do not post the same text across multiple communities on the same day.
- Do not use bots to comment, vote, repost or DM.
- Do not submit to Reddit/HN from a low-trust account; participate first.
- Prefer registry/list submissions over social posting.
