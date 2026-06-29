---
name: droste-memory
description: Use when the chat should save durable knowledge into Droste-Memory, retrieve spatial memory context, pan or zoom the Droste camera, reveal hidden detail layers, or synchronize with the local FastAPI visualizer.
---

# Droste Memory

Use this skill when conversation context should interact with the local
Droste-Memory workspace resolved by the plugin runtime.

## Behavior

- Prefer the plugin MCP tools over direct file edits for Droste memory state.
- Treat Droste-Memory as "context as camera": index broad structure first,
  zoom toward the relevant node, then compile only the needed context.
- When the user asks about a project, repository, folder, codebase, files, or
  architecture that is not already indexed, call `droste_index_project` before
  relying on remembered context.
- For code questions, prefer `droste_zoom_query` followed by
  `droste_get_context` over loading many files into chat.
- Treat dependency wormholes as valid context jumps: when `droste_get_context`
  returns `via_wormhole`, include the linked snippet even if it lives far away
  on the canvas.
- Save durable project facts, decisions, summaries, documentation, and useful
  implementation notes as coordinated memory nodes with `droste_remember`.
- When the user asks to inspect, deepen, zoom, focus, reveal, or move through
  memory, call `droste_focus_node`, `droste_pan_zoom`, or `droste_context_view`.
- Use higher zoom levels only when deeper detail is requested. Hidden
  `detail_content` should remain locked until the camera zoom reaches the
  node's `zoom_threshold`.
- Before answering from remembered spatial context, call `droste_status` or
  `droste_context_view` so the answer reflects the current canvas state.
- If the FastAPI visualizer is running at `http://127.0.0.1:5000`, the MCP
  adapter synchronizes through its API. If it is offline, the adapter writes
  through `core.DrosteConceptEngine` and the next visualizer launch will load
  the persisted JSON state.

## Node Guidelines

- `title`: short, searchable label.
- `summary`: macro-level memory visible from normal zoom.
- `detail_content`: full documentation, evidence, code notes, or reasoning to
  reveal only at deeper zoom.
- `x` and `y`: optional fixed canvas coordinates in `[-1.0, 1.0]`; provide
  both together when a node must stay at an exact spatial point.
- `zoom_threshold`: use `12-20` for ordinary notes, `25-45` for deeper
  implementation detail, and higher values for rarely needed internals.

## Tool Map

- `droste_remember`: create a spatial-fractal memory node.
- `droste_pan_zoom`: move the virtual camera and update visible nodes.
- `droste_focus_node`: focus a node by id or title/query and optionally reveal
  its detail layer.
- `droste_context_view`: get the current canvas, or focus a matching context.
- `droste_status`: inspect macro map, camera, FOV, and visible nodes.
- `droste_visualizer_state`: inspect the dashboard-oriented state payload.
- `droste_index_project`: scan a project and create nested project, directory,
  file, and symbol nodes with source line references and dependency wormholes.
- `droste_zoom_query`: search memory and move the camera to the best matching
  concept at the required zoom level.
- `droste_get_context`: compile only the relevant source/memory snippets within
  a character budget for the current answer.

## Memory Controller Policy

- Index only user-approved local paths or the active workspace.
- Use `droste_index_project(path, reset=false)` to refresh an existing index
  without deleting manual concept nodes.
- Use `reset=true` only when the user asks for a clean rebuild.
- Keep context budgets small by default (`4000-8000` characters). Increase only
  when the user asks for deeper implementation detail.
- Cite source paths and line ranges from `droste_get_context` when explaining
  code decisions.
