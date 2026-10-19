# Changelog

## 1.2.0 — 2026-10-19

- Resolve Python calls through lexical scopes, imports, aliases, re-exports and
  explicit self/cls receivers; omit unknown or ambiguous targets.
- Preserve call-site evidence and resolution methods in stored links.
- Add `find`, `references`, `check`, `watch` and three MCP inspection tools.
- Record source hashes and partial coverage; warn on stale context.
- Batch watcher refreshes across supported languages, including deletions and
  renames, and rebuild incoming links when definitions move.
- Respect nested `.gitignore` and root `.drosteignore`; exclude symlinks and
  the selected database. Collect Java, C#, Ruby, PHP and Python stub files.
- Keep identical code in different modules distinct. Preserve other indexed
  roots and concepts on `index --reset`.
- Separate embedding caches by model identity; invalidate reused node vectors
  when switching backend and fall back to lexical queries until re-indexed.
- Enforce the effective character budget including separators; remove the
  undocumented focus overflow and use source pointers for oversized bodies.
- Add 28 regression cases, installed-wheel calls to all nine MCP tools, and a
  pinned public-source corpus with a documented unresolved receiver case.


## 1.1.7 ? 2026-10-19

- Fix MCP startup on fresh installations: require `mcp>=1.30.0,<2`; MCP 2.x removed `mcp.server.fastmcp`.
- Support both standard property-based and legacy method-based tree-sitter bindings; verify real syntax edges in TypeScript, JavaScript, Go, Rust and C.
- Respect the selected database when exporting the viewer; serve each snapshot from temporary storage.
- Use writable user data paths for installed packages and support `DROSTE_DB`.
- Namespace shards and caches per database; migrate legacy shards on save without deleting originals. Rebuild regenerable caches.
- Add explicit offline hash embeddings via `DROSTE_EMBEDDING_BACKEND=hash`.
- Add Windows CI, distribution-content validation and an installed wheel smoke test covering CLI, HTTP and MCP.
- Fix the GitHub Pages homepage and document current competitors and limitations.


All notable public release notes for Droste are tracked here.

## v1.1.6 - Visualizer Wheel Packaging

- Fixed the PyPI wheel packaging for `droste view` by including the visualizer
  cockpit page, templates, and public demo graph in installed distributions.
- Verified that the public three-command flow works from a clean install:
  `pip install droste-memory`, `droste index .`, `droste view`.
- Keeps the v1.1.5 causal-context improvements while making the visual command
  usable for first-time PyPI users.

## v1.1.5 - Causal Context Delivery

- Prevented self-index contamination by excluding Droste's own `.droste/`,
  `.tmp/`, and `*.egg-info` data directories from project indexing.
- Pinned the focus node's direct `syntax_dependency` callers/callees directly
  after the focus in packed context, so causal neighbours survive realistic
  small budgets before secondary lexical matches.
- Added guaranteed compact stubs for caller/callee neighbours, including
  critical/risky neighbours that previously could vanish when full context did
  not fit.
- Added adversarial regression tests for focus-wormhole pinning and critical
  neighbour stubs. Suite now covers 28 deterministic tests.

## v1.1.4 - Python 3.10/3.11 Compatibility

- Replaced Python 3.12-only `Path.walk()` usage with `os.walk()` so Droste's
  declared Python 3.10+ support is real in CI and installs.
- Hardened CI failure reporting so pytest failures surface as GitHub
  annotations for faster release debugging.
- Added MCP Registry publish release-alignment checks so registry metadata is
  only published after the matching PyPI package is live.

## v1.1.3 - Agent Skill Distribution

- Added public Codex and Claude skill templates in `integrations/` so agents can
  learn Droste's safe indexing and context workflow.
- Documented agent skill installation in the README and growth engine.

## v1.1.2 - MCP Registry Launch Prep

- Added the PyPI README ownership marker required by the official MCP Registry:
  `io.github.lorismascio17/droste-memory`.
- Added `server.json` so Droste can be published as a discoverable MCP server.
- Added a manual GitHub Actions workflow to publish `server.json` to the MCP
  Registry after the matching PyPI release is live.
- Added a scheduled growth radar workflow to keep public discovery state
  visible in GitHub Actions.
- Added a growth engine document with directory, registry and outreach targets.

## v1.1.1 - Packaging and Discovery Hardening

- Hardened package manifests so generated visualizer data (`graph.json`,
  `status.json`, `context.json`) is excluded from source distributions.
- Kept only the public self-demo graph (`visualizer/demo_graph.json`) in the
  packaged project.
- Added PyPI metadata for project discovery: keywords, classifiers, project
  URLs, and SPDX license metadata.
- Added README badges for PyPI version, supported Python versions, CI, and MIT
  license.

## v1.1.0 - MCP Guardrails

- Added MCP root isolation: indexed repositories are tagged as the active root,
  and MCP context/status calls filter to that root by default.
- Added clean warnings for unsafe multi-root databases instead of silently
  mixing context from unrelated repositories.
- Added Windows UTF-8 CLI output guard to prevent terminal encoding crashes.
- Added query-aware ranking that prefers runtime source paths unless the query
  explicitly asks for tests or documentation.
- Expanded deterministic tests for root isolation, ranking, UTF-8 safety, shard
  persistence, token packing, and seqlock race protection.

## v1.0.1 - Public MCP Entry Point

- Published the package on PyPI as `droste-memory`.
- Added the global CLI entry point: `droste`.
- Added the MCP server command: `droste mcp`.
- Documented Codex, Cursor, and Claude-style MCP configuration examples.
