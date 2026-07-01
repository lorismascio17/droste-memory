# Changelog

All notable public release notes for Droste are tracked here.

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

