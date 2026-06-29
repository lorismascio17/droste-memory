# Droste Memory Codex Plugin

This repo-local Codex plugin connects the current workspace to the
Droste-Memory spatial-fractal engine.

## Components

```text
plugins/droste-memory/
|-- .codex-plugin/plugin.json
|-- .mcp.json
|-- skills/droste-memory/SKILL.md
`-- scripts/
    |-- droste_codex_mcp.py
    `-- run_mcp.ps1
```

## MCP Runtime

The plugin starts `scripts/run_mcp.ps1`, which locates Python, sets
`PYTHONPATH` for the workspace, and runs `scripts/droste_codex_mcp.py`.
The adapter implements the small MCP stdio surface it needs directly, so it
does not require the external `mcp` Python package to start.

The MCP adapter prefers the FastAPI visualizer at `http://127.0.0.1:5000`.
When the visualizer is running, writes go through `/api/inject` and
`/api/camera`, keeping the dashboard process synchronized in real time. When
it is offline, the adapter falls back to `core.DrosteConceptEngine` and writes
`droste_memory_db.json` directly.

Indexed projects use a deterministic radial-fractal layout: directories,
files, and symbols are placed on rigid concentric circles around their parent
node. The ingester also records dependency wormholes between symbols, and
`droste_get_context` can follow those links to include useful cross-file
snippets inside the requested context budget.

## Tools

- `droste_remember`: save a node with summary, detail content, zoom gate, and
  optional fixed `x`/`y` coordinates.
- `droste_pan_zoom`: move the virtual camera and return visible nodes.
- `droste_focus_node`: focus by node id or text query and optionally reveal.
- `droste_context_view`: inspect the current canvas or focus a query.
- `droste_status`: return macro map, FOV, camera, and visible nodes.
- `droste_visualizer_state`: return the visualizer-oriented state payload.
- `droste_index_project`: index a local codebase into project, directory, file,
  and symbol nodes.
- `droste_zoom_query`: search memory and move the camera to the needed concept.
- `droste_get_context`: compile only relevant snippets within a character
  budget.
