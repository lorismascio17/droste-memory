# Droste Growth Engine

This is the operating plan for making Droste discoverable without spam.

The goal is not to auto-post across social networks. The goal is to make Droste
present wherever developers and MCP clients search for code-memory tools.

## Prime Directive

- Automate verification, packaging and registry publishing.
- Prepare high-quality submissions and outreach copy.
- Do not automate unsolicited posts, votes, comments, DMs or cross-posting.
- Do not ask for stars. Ask for technical feedback.

## Primary Distribution Channels

| Priority | Channel | Why it matters | Action |
| --- | --- | --- | --- |
| P0 | Official MCP Registry | Discovery by MCP clients and directories | Publish `server.json` with `mcp-publisher` |
| P0 | GitHub topic search | Permanent organic discovery | Keep topics, README and badges sharp |
| P0 | PyPI | Install and metadata discovery | Keep package metadata current |
| P1 | Glama MCP directory | Large MCP server directory | Submit or claim after MCP Registry publish |
| P1 | awesome-mcp-servers | High-signal GitHub list | Open a focused PR |
| P1 | Dev.to technical article | Searchable explanation | Keep article linked from launch posts |
| P1 | Codex and Claude skill templates | Agent-side adoption path | Promote `integrations/*-skill/droste-code-memory` |
| P1 | Platform integration brief | Maintainer-facing adoption path | Share `docs/PLATFORM_INTEGRATION_BRIEF.md` with Codex/Claude/MCP maintainers |
| P2 | Reddit/HN | Feedback once account trust exists | Participate first, then post |
| P2 | Direct outreach | High-quality maintainer feedback | 10 targeted messages, no mass DMs |

## Official MCP Registry Path

Droste now has:

- `server.json`
- PyPI package: `droste-memory`
- README marker: `mcp-name: io.github.lorismascio17/droste-memory`
- workflow: `.github/workflows/publish-mcp-registry.yml`

Publish order:

1. Release the matching version to PyPI.
2. Run the `Publish MCP Registry` workflow manually from GitHub Actions, or let
   its daily schedule catch the release after PyPI is live.
3. The workflow first runs `scripts/check_release_alignment.py`, so it skips
   cleanly until `pyproject.toml`, `server.json` and the public PyPI release all
   point to the same version.
4. Verify discovery:

```bash
python scripts/growth_radar.py
```

After the radar reports `MCP Registry visible: yes`, use
`docs/SUBMISSION_PACK.md` for directory submissions and awesome-list PR copy.

## GitHub Release Copy

Title:

```text
Droste v1.1.6 - Visualizer wheel packaging
```

Body:

```text
Droste v1.1.6 fixes the PyPI wheel packaging for `droste view`, so a clean install can run the full three-command flow.

Highlights:
- Ships the visualizer cockpit, templates, and public demo graph in the wheel.
- Keeps the v1.1.5 causal-context fixes: self-index protection, pinned caller/callee neighbours, and compact causal stubs.
- Keeps Codex and Claude skill templates available for agent-side adoption.

Install:
python -m pip install --upgrade droste-memory

View:
droste index .
droste view

MCP:
droste mcp

Server name:
io.github.lorismascio17/droste-memory
```

## Awesome List PR Entry

Suggested entry:

```markdown
- [Droste](https://github.com/lorismascio17/droste-memory) - Local MCP code-memory engine for AI coding agents. Builds a structural + semantic graph of a repository, then returns causal context through CLI and MCP.
```

PR body:

```text
Adds Droste, an open-source local MCP code-memory engine for AI coding agents.

It indexes a repository into a structural + semantic graph and exposes context retrieval through MCP, with sharded local storage and project-root isolation.
```

## Agent Skill Distribution

The public Codex skill lives at:

```text
integrations/codex-skill/droste-code-memory/
```

The Claude-compatible skill lives at:

```text
integrations/claude-skill/droste-code-memory/
```

Suggested install copy:

```text
Copy integrations/codex-skill/droste-code-memory into ~/.codex/skills/ and ask:
"Use $droste-code-memory to index this repository and retrieve causal context before editing."
```

For Claude, import or copy `integrations/claude-skill/droste-code-memory/` as a
Claude Skill package. This gives Droste an agent-native adoption path separate
from social posting.

## Platform Integration

Use `docs/PLATFORM_INTEGRATION_BRIEF.md` when contacting MCP client maintainers,
agent-skill directories, Anthropic/Claude ecosystem maintainers, Codex ecosystem
maintainers or code-intelligence tool authors. It is intentionally technical:
the ask is evaluation, listing or integration feedback, not promotion.

## Direct Outreach Copy

```text
Hi, I built Droste, a local MCP code-memory engine for AI coding agents.

It combines structural code graph retrieval with semantic search, so agents can retrieve caller/callee and cross-language context instead of only vector-similar chunks.

I am not asking for promotion; I would value technical feedback from someone working in MCP, code intelligence or agent tooling.

GitHub:
https://github.com/lorismascio17/droste-memory
```

## Weekly Radar

The `Growth Radar` GitHub Action runs every Monday and can also be triggered
manually. It checks:

- GitHub stars/topics/latest push;
- PyPI latest version;
- MCP Registry visibility.

It intentionally does not post anywhere.
