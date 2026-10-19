---
name: droste-code-memory
description: Use when an agent needs project-aware code memory, MCP context retrieval, repository indexing, causal code search, or Droste CLI workflows. Triggers include requests to index a repo with Droste, retrieve context with `droste context`, configure `droste mcp`, inspect caller/callee relationships, avoid blind file reads, or keep AI coding context isolated per repository.
---

# Droste Code Memory

Use Droste as the local structural + semantic memory layer before broad file
reading. Prefer it when the task needs codebase context, symbol relationships,
or MCP-backed retrieval.

## Core Workflow

1. Check Droste is available:

```bash
droste --version
```

If uncertain, run `scripts/check_droste.py` from this skill.

2. Use an isolated DB for serious work. Put `--db` before the command:

```bash
droste --db /absolute/path/to/droste_memory_db.json status
droste --db /absolute/path/to/droste_memory_db.json index . --reset
```

3. Index the current repo before asking for context:

```bash
droste index .
```

Use `--reset` to force re-extraction of the selected project; other roots and manual concepts are preserved.

4. Retrieve focused context:

```bash
droste context "authentication flow" --budget 3000 --json
```

Use context results to choose precise files/symbols to inspect. Do not use
Droste output as the only evidence for final claims; inspect relevant files.

5. Start MCP when configuring an agent client:

```bash
droste mcp
```

Read `references/mcp-config.md` when writing Codex, Claude Desktop, Cursor, or
JSON MCP client configuration.

## Safety Rules

- Confirm the selected project root before forcing a re-index. Use one writer per database.
- Do not index secrets, dependency caches, build output, `.droste/`, or private
  generated graph JSON.
- If `droste_status` or `droste context --json` reports multi-root warnings,
  pass an explicit `--db` or `root` instead of accepting mixed context.
- Prefer source paths under the current repo; treat external paths as suspicious
  until verified.
- Never paste full secrets from retrieved context. Redact before reporting.

## Useful Commands

```bash
droste status --json
droste check --json
droste find "symbolName" --json
droste references "symbolName" --direction both --json
droste watch .
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
- `droste_check_index` to verify source hashes and partial coverage.
- `droste_find_symbols` to locate IDs, qualified names and source lines.
- `droste_references` to inspect static callers/callees with evidence.

Re-index before using stale or unverifiable results. If coverage is partial,
increase the relevant index limits or explicitly acknowledge the omitted files.
For ambiguous references, choose a candidate ID or exact file instead of guessing.
A missing reference does not establish that code is unused. Context budgets
are characters (minimum 500); JSON metadata is outside that budget.

Index first, then query. If the agent has an explicit project root, pass it.

