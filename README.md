<div align="center">

# Droste

<!-- mcp-name: io.github.lorismascio17/droste-memory -->

### Explore your codebase and give agents source-backed context.

Droste indexes source files into a local symbol graph and a zoomable map.
Find definitions, inspect static callers and callees, check whether the index
is fresh, and compile selected code into a bounded context for an AI agent.

**Local-first · zero-config · polyglot · MCP-native**

[![PyPI](https://img.shields.io/pypi/v/droste-memory.svg?cacheSeconds=300)](https://pypi.org/project/droste-memory/)
[![Python](https://img.shields.io/pypi/pyversions/droste-memory.svg)](https://pypi.org/project/droste-memory/)
[![CI](https://github.com/lorismascio17/droste-memory/actions/workflows/ci.yml/badge.svg)](https://github.com/lorismascio17/droste-memory/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

![Droste fractal code galaxy](docs/assets/hero.gif)

*Explore the indexed file and symbol relationships.
[Full flythrough (FastAPI)](docs/assets/demo.mp4)*

[Live demo](https://lorismascio17.github.io/droste-memory/) ? [Quickstart](#quickstart) · [Why it's different](#why-its-different) · [How it works](#how-it-works) · [MCP](#use-it-as-an-mcp-server) · [Benchmarks](#benchmarks)

</div>

---

## Quickstart

Install **v1.2.0** from [PyPI](https://pypi.org/project/droste-memory/)
or [GitHub Releases](https://github.com/lorismascio17/droste-memory/releases).
See [release status](docs/RELEASING.md).

```bash
# Windows
python -m pip install --upgrade droste-memory

# macOS / Linux
python3 -m pip install --upgrade droste-memory

droste index . # index the current repo
droste view    # open the fractal galaxy in your browser
```

Install once, then index and view. `droste view` opens a full-screen, zoomable map of your
code — scroll to dive from the project star into folder orbits, down to the
individual functions and their indexed relationships.

Need it for an agent instead of your eyes?

```bash
droste context "checkout flow" --budget 1500
```

For everyday code investigation:

```bash
droste check                         # verify source hashes and index coverage
droste find calculate                # find symbol IDs, paths and line numbers
droste references calculate --direction both
droste references helper --file src/math.py --direction callers --depth 3
droste watch .                       # refresh after additions, edits, renames or deletions
```

`references` returns candidates for ambiguous names; choose a file or symbol ID
instead of guessing. Python links include the call expression and source line.
Other language links report their heuristic resolution method. `check` exits
with status 1 for stale, partial or unverifiable indexes. Context responses warn
about stale/partial coverage using a quick size/mtime check; `check` additionally
verifies SHA-256 hashes. Run it before relying on an old index.

Indexing respects nested `.gitignore` rules and a project-root `.drosteignore`
(Git ignore syntax; applied last). Symlinks, generated bundles, dependency
directories and Droste's own storage are excluded. Use an explicit `--db` per
project when running independent writers. `watch` batches root refreshes and
reuses unchanged extraction/embeddings; `view` remains a snapshot, so reopen it
to see a refreshed graph.

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
Local Graph Engine v1.2.0-Alpha-Sharded

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

The useful building blocks are:

- **Explainable static references.** Python calls use lexical scopes, import
  aliases and explicit `self`/`cls` members. Dynamic receivers remain unresolved.
  Other languages use local/unique-name matching; ambiguous candidates are skipped.
- **Cross-language bridges.** Droste links across
  languages — app code to SQL functions/tables (`.rpc('x')`, `.from('table')`),
  to edge functions, and same-name handlers between any two languages. Your
  Dart/TS/Python frontend and your database stop being two separate worlds on the
  map.
- **Visual exploration.** Navigate files, symbols and indexed relationships in
  an interactive radial map.
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

The [public source validation](docs/VALIDATION.md) uses pinned Requests and
CPython revisions with manually labelled edge assertions. It currently passes
19 of 20 assertions: 15/16 expected edges found, 0/4 forbidden edges emitted.
The missed edge is documented; this small corpus is not an agent-task benchmark
or a comparison against competing products.

```bash
python eval/public_corpus.py --output public-results.json
```

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

- **Static graph.** Python scopes and imports resolve named calls; tree-sitter
  supplies definitions/references for other supported languages. Their
  name-based links and cross-language DB/string bridges are heuristics, labelled
  in reference results. No runtime behavior or complete type resolution is implied.
- **Hybrid seed.** A query is matched by a normalized blend of lexical score and
  semantic cosine (fastembed `bge-small-en-v1.5`, 384-dim), then the graph
  expands the seed bidirectionally (callees and callers).
- **Context packer.** The budget is characters, with a minimum of 500. Full
  source, contracts or explicit source stubs fit that effective limit, including
  separators. No tokenizer-specific token count is promised.
- **Sharded persistence.** One shard per file under a database-specific `.droste/` directory, blake2b
  dirty-tracking so a re-index rewrites only what changed; atomic file replacement with metadata
  written last. Multi-file updates are not transactions; use one writer per DB
  and re-index after an interrupted write.

---

## Use it as an MCP server

Droste is a drop-in MCP server — an AI agent can call it as primary code memory instead of doing blind file reads. Add this to your client configuration file (e.g., Cursor, Claude Desktop, or Codex):

First install or upgrade to the current PyPI release:

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

Restart your client after changing the MCP config. Index the project, call
`droste_check_index`, then use symbol lookup and references before retrieving
context. Inspect the real source before making changes.

Primary tools: `droste_index_project`, `droste_check_index`,
`droste_find_symbols`, `droste_references`, `droste_get_context`, `droste_status`.
Supporting canvas tools: `inject_concept`, `move_camera_and_zoom`, `get_space_status`.

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

**v1.2.0 (alpha).** Tested CLI/MCP workflows, source freshness checks and an
explicitly limited static graph. Dynamic dispatch, receiver-type inference and
complete cross-language resolution remain outside the current guarantees.
Use one writer per database. See [validation and limitations](docs/VALIDATION.md).

### What's new in v1.2.0

- Fix Python scope/import resolution and stop guessing ambiguous name matches.
- Add `find`, `references`, `check` and matching MCP tools, with source evidence.
- Refresh all supported languages after edits, additions, deletions and renames.
- Report partial coverage and stale context; preserve other roots during `--reset`.
- Respect ignore rules and actually collect Java, C#, Ruby and PHP files.
- Separate cached embeddings by model identity and rebuild when switching backends.
- Add independent public-source assertions and exercise all nine MCP tools.

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
