<div align="center">

# Droste

<!-- mcp-name: io.github.lorismascio17/droste-memory -->

### See your codebase as a living galaxy — and give your agents causal memory of it.

Droste indexes any repo into a fractal, zoomable map of its symbols, wires them
together with their real call / import / DB edges across languages, and serves an
agent the *causal* slice of code it actually needs — not just keyword matches.

**Local-first · zero-config · polyglot · MCP-native**

[![PyPI](https://img.shields.io/pypi/v/droste-memory.svg)](https://pypi.org/project/droste-memory/)
[![Python](https://img.shields.io/pypi/pyversions/droste-memory.svg)](https://pypi.org/project/droste-memory/)
[![CI](https://github.com/lorismascio17/droste-memory/actions/workflows/ci.yml/badge.svg)](https://github.com/lorismascio17/droste-memory/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/github/license/lorismascio17/droste-memory.svg)](LICENSE)

![Droste fractal code galaxy](docs/assets/hero.gif)

*Zooming out reveals the causal web — every cyan arc is a real `syntax_dependency`
edge. [Full flythrough (FastAPI)](docs/assets/demo.mp4)*

[Quickstart](#quickstart) · [Why it's different](#why-its-different) · [How it works](#how-it-works) · [MCP](#use-it-as-an-mcp-server) · [Benchmarks](#benchmarks)

</div>

---

## Quickstart

```bash
# Windows
python -m pip install --upgrade droste-memory

# macOS / Linux
python3 -m pip install --upgrade droste-memory

droste index . # index the current repo
droste view    # open the fractal galaxy in your browser
```

Install once, then index and view. `droste view` opens a full-screen, 60fps zoomable map of your
code — scroll to dive from the project star into folder orbits, down to the
individual functions, with the causal edges glowing between them.

Need it for an agent instead of your eyes?

```bash
droste context "checkout flow" --budget 1500   # causal context slice for an LLM
```

Running `droste` with no arguments prints the command palette:

```text
                  .-----------------------.
             .----'           |           '----.
         .---'          .-----+-----.          '---.
       .'          .----'     |     '----.          '.
      /        .---'      .---+---.      '---.        \
     /      .-'        .-'    |    '-.        '-.      \
    |     .'        .-'   .---+---.   '-.        '.     |
    |    /        .'    .'    |    '.    '.        \    |
    |   |        |     |   .--+--.   |     |        |   |
    | --+--------+-----+---+  @  +---+-----+--------+-- |
    |   |        |     |   '--+--'   |     |        |   |
    |    \        '.    '.    |    .'    .'        /    |
    |     '.        '-.   '---+---'   .-'        .'     |
     \      '-.        '-.    |    .-'        .-'      /
      \        '---.      '---+---'      .---'        /
       '.          '----.     |     .----'          .'
         '---.          '-----+-----'          .---'
             '----.           |           .----'
                  '-----------------------'

DROSTE-MEMORY // RIGID FRACTAL RADIAL LAYOUT
Local Graph Engine v1.1.4-Alpha-Sharded

Commands
  droste index <path> [--reset]
  droste status
  droste zoom <symbol_name>
  droste context [query] --budget 1500
  droste mcp

Fast path: droste context hub_core --budget 1000 | clip
```

---

## Why it's different

Most "code context" tools rank by keyword (ctags / ripgrep / repo-maps) or by
embedding cosine (vector-RAG). Both can only return what *resembles* your query.
A caller that shares no tokens — or a database function in a different language —
is invisible to them, yet it's exactly what you need to understand or change the
code.

Droste's edge is the causal graph:

- **Causal wormholes.** Real `syntax_dependency` edges (calls, imports,
  inheritance) in both directions — Droste hands the caller and callees, ordered,
  within a token budget.
- **Cross-language bridges.** The part nobody else does well: Droste links across
  languages — app code to SQL functions/tables (`.rpc('x')`, `.from('table')`),
  to edge functions, and same-name handlers between any two languages. Your
  Dart/TS/Python frontend and your database stop being two separate worlds on the
  map.
- **A map you actually want to look at.** The fractal galaxy isn't a gimmick —
  it's how you see coupling, risk hotspots, and the blast radius of a change.
- **Zero-config and local.** No cloud, no account, no API key. fastembed (ONNX,
  no torch) gives real semantics; a deterministic fallback keeps it runnable
  anywhere.

Polyglot: Python (AST) + tree-sitter for Dart, TypeScript/JavaScript, Go, Rust,
Java, C#, C/C++, Kotlin, Swift, Ruby, PHP, SQL — symbols *and* edges.

> **Honest scope:** the measured advantage is structural / causal retrieval. On
> pure semantic "concept" queries it's competitive with a vector baseline, not a
> leap. Cross-language bridges are strongest where the target is actually defined
> in the indexed repo (e.g. SQL schema in your migrations).

---

## Benchmarks

Self-supervised eval (gold = the true caller/callee set from the AST), equal
retrieval breadth *k*, real embeddings, across Python + Dart repos
(`eval/comparative_eval.py`):

| structural retrieval | Droste | vector-RAG core | lexical core |
| --- | --- | --- | --- |
| neighbour-recall | **0.94** | 0.18 | 0.42 |
| nDCG@k | **0.65** | 0.10 | 0.29 |

…plus hundreds of true causal neighbours that both baselines structurally miss.
This is a retrieval-method comparison (the cores of vector-RAG and lexical
search), not a head-to-head against the finished products that wrap them.

---

## How it works

- **Causal graph.** Each definition is parsed (Python `ast`; tree-sitter for the
  rest) into the names it calls / imports / inherits, becoming first-class
  `syntax_dependency` edges. Cross-language edges add DB calls (`.rpc`, `.from`,
  `.functions.invoke`) and string-literal name matches across languages.
- **Hybrid seed.** A query is matched by a normalized blend of lexical score and
  semantic cosine (fastembed `bge-small-en-v1.5`, 384-dim), then the graph
  expands the seed bidirectionally (callees and callers).
- **Token packer.** Results fit a budget with LOD-demotion (full to contract to
  skeleton) and a hard guardrail that never cuts a line of code mid-token.
- **Sharded persistence.** One shard per file under `.droste/`, blake2b
  dirty-tracking so a re-index rewrites only what changed; atomic writes + meta
  written last, so it is crash-safe and self-heals on the next run.

---

## Use it as an MCP server

Droste is a drop-in MCP server — an AI agent can call it as primary code memory instead of doing blind file reads. Add this to your client configuration file (e.g., Cursor, Claude Desktop, or Codex):

First install or upgrade the PyPI package:

```bash
# Windows
python -m pip install --upgrade droste-memory

# macOS / Linux
python3 -m pip install --upgrade droste-memory

droste mcp --help
```

For Codex, add this to `C:\Users\<you>\.codex\config.toml` on Windows, or `~/.codex/config.toml` on macOS/Linux:

```toml
[mcp_servers.droste]
command = "droste"
args = ["mcp"]
startup_timeout_sec = 120
```

By default, `droste mcp` uses Droste's global local database. That is fine for
quick use and small workflows. For serious multi-repo work, use one database per
repository so each project has isolated memory and agents can safely re-index
that project with `reset=true`.

The `--db` option is global, so keep it before `mcp`:

```toml
# Windows example
[mcp_servers.droste]
command = "droste"
args = ["--db", "C:/Users/you/AppData/Local/Droste/my-project/droste_memory_db.json", "mcp"]
startup_timeout_sec = 120
```

```toml
# macOS / Linux example
[mcp_servers.droste]
command = "droste"
args = ["--db", "/Users/you/.local/share/droste/my-project/droste_memory_db.json", "mcp"]
startup_timeout_sec = 120
```

For JSON-based MCP clients:

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

The same isolated-DB pattern works in JSON clients:

```json
{
  "mcpServers": {
    "droste": {
      "command": "droste",
      "args": [
        "--db",
        "/absolute/path/to/droste_memory_db.json",
        "mcp"
      ]
    }
  }
}
```

Restart your client after changing the MCP config. In a repo, ask your agent to call `droste_index_project` first, then `droste_get_context` for causal context.

Key tools: `droste_index_project`, `droste_get_context`, `droste_status`.

---

## Use it as an agent skill

Droste also ships agent skill templates for Codex and Claude. They teach an
agent how to use Droste safely: isolated DBs, indexing, `droste context`, MCP
config, and root-contamination guardrails.

Codex skill:

```text
integrations/codex-skill/droste-code-memory/
```

Claude-compatible skill:

```text
integrations/claude-skill/droste-code-memory/
```

Install the Codex skill by copying the folder into your Codex skills directory:

```bash
# macOS / Linux
mkdir -p ~/.codex/skills
cp -R integrations/codex-skill/droste-code-memory ~/.codex/skills/
```

```powershell
# Windows PowerShell
New-Item -ItemType Directory -Force "$env:USERPROFILE\.codex\skills" | Out-Null
Copy-Item -Recurse -Force integrations\codex-skill\droste-code-memory "$env:USERPROFILE\.codex\skills\"
```

Then ask Codex:

```text
Use $droste-code-memory to index this repository and retrieve causal context before editing.
```

For Claude, import or copy the Claude skill folder as a Claude Skill package and
keep its `SKILL.md`, `references/`, and `scripts/` together.

---

## Development

```bash
pip install -e ".[dev]"
pytest                            # deterministic regression suite (tests/)
python eval/comparative_eval.py   # retrieval benchmark vs lexical & vector cores
```

`tests/` = invariants + concurrency (round-trip, dirty-oracle, packer guardrail,
cross-process shard race). `eval/` = performance/quality benchmarks.

---

## Distribution

Launch and directory submission materials live in:

- `docs/GROWTH_ENGINE.md`
- `docs/SUBMISSION_PACK.md`
- `docs/LAUNCH_KIT.md`

Run the read-only radar before submitting anywhere:

```bash
python scripts/growth_radar.py
```

---

## Status

**v1.1.4 (alpha).** Engine, polyglot + cross-language graph, CLI, fractal
visualizer and MCP server are working and tested. Packaging/distribution are
maturing — issues and PRs welcome (see `CONTRIBUTING.md`).

### What's new in v1.1.4

- Fixed Python 3.10/3.11 compatibility by replacing Python 3.12-only
  `Path.walk()` usage.
- Hardened MCP Registry publishing so metadata is only published after the
  matching PyPI release is live.
- Improved CI diagnostics for faster multi-version release debugging.

### What's new in v1.1.3

- Added public Codex and Claude skill templates for agent-side Droste adoption.

### What's new in v1.1.2

- Added MCP Registry ownership metadata in the README/PyPI description.
- Added `server.json` for official MCP Registry publishing.
- Added manual and scheduled growth workflows for MCP registry publishing and
  visibility checks.

### What's new in v1.1.1

- Packaging/privacy hardening: generated visualizer JSON files (`graph.json`,
  `status.json`, `context.json`) are excluded from source distributions, while
  the public `visualizer/demo_graph.json` remains included.

### What's new in v1.1.0

- MCP context is root-isolated: `droste_index_project` records the active repo,
  and `droste_get_context` / `droste_status` filter to that root unless an agent
  passes another `root` explicitly.
- Multi-root databases no longer silently mix repositories when no safe root can
  be inferred; Droste returns a clean warning instead.
- Windows CLI output is guarded for UTF-8 consoles to avoid `UnicodeEncodeError`
  crashes on older terminal encodings.
- Retrieval ranking is now query-aware: runtime code gets a slight boost for
  normal implementation queries, while tests/docs remain fully visible when the
  query asks for them.

## License

MIT — see [LICENSE](LICENSE).
