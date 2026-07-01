---
name: droste-code-memory
description: Use when Claude needs project-aware code memory, MCP context retrieval, repository indexing, causal code search, or Droste CLI workflows. Triggers include requests to index a repo with Droste, configure `droste mcp`, retrieve graph-aware context, inspect caller/callee relationships, avoid blind file reads, or keep coding-agent context isolated per repository.
---

# Droste Code Memory

Use Droste as Claude's local structural + semantic memory layer for codebases.
Prefer it before broad file reads when the task needs repository context,
symbol relationships, caller/callee edges, or MCP-backed retrieval.

## Core Workflow

1. Confirm Droste is installed:

```bash
droste --version
```

If uncertain, run `scripts/check_droste.py` from this skill.

2. Use an isolated DB for serious multi-repo work. Put `--db` before the
command:

```bash
droste --db /absolute/path/to/droste_memory_db.json status
droste --db /absolute/path/to/droste_memory_db.json index . --reset
```

3. Index the current repo before asking for context:

```bash
droste index .
```

Use `--reset` only when the selected DB belongs to the current repository.

4. Retrieve focused context:

```bash
droste context "authentication flow" --budget 3000 --json
```

Use Droste output to choose exact files and symbols to inspect. Do not make final
claims from retrieved context alone; verify relevant source files.

5. Use MCP for persistent Claude workflows:

```bash
droste mcp
```

Read `references/claude-mcp-config.md` when configuring Claude Desktop,
Claude Code, or another Claude-facing MCP client.

## Safety Rules

- Do not reset a shared/global Droste DB unless the user explicitly asked.
- Do not index secrets, dependency caches, build output, `.droste/`, or private
  generated graph JSON.
- If Droste reports multi-root warnings, pass an explicit DB or root instead of
  accepting mixed context.
- Prefer source paths under the current repository. Treat unrelated absolute
  paths as suspicious until verified.
- Redact any secret-like value before reporting it.

## Useful Commands

```bash
droste status --json
droste context "query" --budget 1500
droste context "query" --budget 3000 --json
droste zoom "symbolName" --no-open
droste view
```

## Expected MCP Tools

When the Droste MCP server is connected, use:

- `droste_index_project` to index the current repository.
- `droste_get_context` to retrieve causal context for a query.
- `droste_status` to inspect storage, active root, symbols and warnings.

Index first, then query. If Claude knows the project root, pass it explicitly.

