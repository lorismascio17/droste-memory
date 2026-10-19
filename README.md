<div align="center">

# Droste

<!-- mcp-name: io.github.lorismascio17/droste-memory -->

### See your codebase as a living galaxy — and give your agents causal memory of it.

Droste indexes any repo into a fractal, zoomable map of its symbols, wires them
together with their real call / import / DB edges across languages, and serves an
agent the *causal* slice of code it actually needs — not just keyword matches.

**Local-first · zero-config · polyglot · MCP-native**

[![PyPI](https://img.shields.io/pypi/v/droste-memory.svg?cacheSeconds=300)](https://pypi.org/project/droste-memory/)
[![Python](https://img.shields.io/pypi/pyversions/droste-memory.svg)](https://pypi.org/project/droste-memory/)
[![CI](https://github.com/lorismascio17/droste-memory/actions/workflows/ci.yml/badge.svg)](https://github.com/lorismascio17/droste-memory/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

![Droste fractal code galaxy](docs/assets/hero.gif)

*Zooming out reveals the causal web — every cyan arc is a real `syntax_dependency`
edge. [Full flythrough (FastAPI)](docs/assets/demo.mp4)*

[Live demo](https://lorismascio17.github.io/droste-memory/) ? [Quickstart](#quickstart) · [Why it's different](#why-its-different) · [How it works](#how-it-works) · [MCP](#use-it-as-an-mcp-server) · [Benchmarks](#benchmarks)

</div>

---

## Quickstart

The fixed **v1.1.7** wheel is available from [GitHub Releases](https://github.com/lorismascio17/droste-memory/releases/tag/v1.1.7).
PyPI and the MCP Registry still list v1.1.6: the new PyPI upload is blocked by
missing Trusted Publisher configuration. The commands below install the fixed
GitHub release directly. See [release status](docs/RELEASING.md).

```bash
# Windows
python -m pip install --upgrade https://github.com/lorismascio17/droste-memory/releases/download/v1.1.7/droste_memory-1.1.7-py3-none-any.whl

# macOS / Linux
python3 -m pip install --upgrade https://github.com/lorismascio17/droste-memory/releases/download/v1.1.7/droste_memory-1.1.7-py3-none-any.whl

droste index . # index the current repo
droste view    # open the fractal galaxy in your browser
```

Install once, then index and view. `droste view` opens a full-screen, zoomable map of your
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
Local Graph Engine v1.1.6-Alpha-Sharded

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

Droste combines lexical and semantic retrieval with a static code graph and an
interactive map. A graph can bring callers and callees into context even when
they share few words with the query. Other tools also offer graph-based MCP
retrieval; see the [dated ecosystem review](docs/COMPETITORS.md).

Droste focuses on a small Python CLI, budgeted context, and visual exploration.
The graph is a static approximation: name matching and cross-language bridges
can produce false positives and do not prove runtime behavior.

Droste's edge is the causal graph:

- **Causal wormholes.** Real `syntax_dependency` edges (calls, imports,
  inheritance) in both directions — Droste hands the caller and callees, ordered,
  within a token budget.
- **Cross-language bridges.** Droste links across
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

The evaluation script compares structural retrieval against lexical and vector
baselines, using caller/callee relations extracted from the same code as the
reference. This measures graph-neighbor retrieval, not agent task completion or
superiority over another product. Previous headline numbers are omitted until
we publish a fixed public corpus, pinned revisions and complete result files.

```bash
python eval/comparative_eval.py --repos /path/to/public/repo --sample 60 --seed 7 --json results.json
```

Use the real embedding backend for semantic comparisons; the offline hash mode
is deterministic but does not provide semantic similarity.

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
- **Sharded persistence.** One shard per file under a database-specific `.droste/` directory, blake2b
  dirty-tracking so a re-index rewrites only what changed; atomic file replacement with metadata
  written last. Multi-file updates are not transactions; use one writer per DB
  and re-index after an interrupted write.

---

## Use it as an MCP server

Droste is a drop-in MCP server — an AI agent can call it as primary code memory instead of doing blind file reads. Add this to your client configuration file (e.g., Cursor, Claude Desktop, or Codex):

First install or upgrade to the fixed GitHub release:

```bash
# Windows
python -m pip install --upgrade https://github.com/lorismascio17/droste-memory/releases/download/v1.1.7/droste_memory-1.1.7-py3-none-any.whl

# macOS / Linux
python3 -m pip install --upgrade https://github.com/lorismascio17/droste-memory/releases/download/v1.1.7/droste_memory-1.1.7-py3-none-any.whl

droste mcp --help
```

For Codex, add this to `C:\Users\<you>\.codex\config.toml` on Windows, or `~/.codex/config.toml` on macOS/Linux:

```toml
[mcp_servers.droste]
command = "droste"
args = ["mcp"]
startup_timeout_sec = 120
```

Installed packages store their default database in `%LOCALAPPDATA%/Droste` on
Windows, `~/Library/Application Support/Droste` on macOS, or
`${XDG_DATA_HOME:-~/.local/share}/droste` on Linux. Source checkouts preserve
their repository-local database. Set `DROSTE_DB` or pass `--db` to override.
Existing databases inside an older installation are not moved automatically;
point `--db` at the old file to continue using it, or re-index your source.

For explicitly offline operation, set `DROSTE_EMBEDDING_BACKEND=hash`.
The default `auto` backend may download an embedding model on first use;
code is processed locally. Hash mode trades semantic retrieval for deterministic,
network-free embedding. `droste view` serves a snapshot; rerun it after indexing.

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

## Status

**v1.1.7 (alpha).** Engine, polyglot + cross-language graph, CLI, fractal
visualizer and MCP server are working and tested. Packaging/distribution are
maturing — issues and PRs welcome (see `CONTRIBUTING.md`).

### What's new in v1.1.7

- Restored fresh-install MCP startup by requiring the compatible 1.x SDK.
- Restored polyglot syntax edges with the current tree-sitter Python API.
- Fixed `droste --db ... view` to visualize the requested database.
- Viewer snapshots use temporary storage, including with read-only installations.
- Isolated shards, registries and embedding caches for databases sharing a folder.
- Installed packages use writable user data storage; `DROSTE_DB` overrides it.
- Added Windows CI, wheel-content checks and an installed CLI/viewer/MCP smoke test.
- Restored the demo homepage and refreshed the competitive positioning.

### What's new in v1.1.6

- Fixed the PyPI wheel packaging for `droste view` by shipping the visualizer
  HTML/templates and public demo graph inside the installed package.
- Verified the three-command pitch from a clean install:
  `pip install droste-memory`, `droste index .`, `droste view`.

### What's new in v1.1.5

- Fixed self-index contamination by excluding Droste's own `.droste/`, `.tmp/`,
  and `*.egg-info` directories from project scans.
- Improved small-budget context packing by pinning the focus node's direct
  callers/callees before secondary lexical matches.
- Guaranteed compact stubs for true causal neighbours so important wormholes do
  not disappear when full context cannot fit.

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
