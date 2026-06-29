# Contributing to Droste

Thanks for helping build the causal-memory layer for AI agents.

## Dev setup

```bash
git clone <your fork>
cd droste-memory
pip install -e ".[dev]"
pytest                 # should be green before you start
```

## Ground rules

- **Tests stay green.** `pytest` runs the deterministic regression suite
  (`tests/`). Add a test for any behaviour change to the engine, ingester, or
  packer. The suite forces the deterministic hash embedding backend, so it runs
  offline with no model download.
- **`eval/` is for benchmarks, `tests/` is for invariants.** Don't mix them.
- **Keep the zero-config moat.** New required deps are a big deal — prefer
  optional extras. `fastembed` (no torch) and `tree-sitter-language-pack` are the
  only heavy runtime deps and both degrade gracefully if missing.
- **Never commit user data.** `droste_memory_db.json`, `.droste/`,
  `visualizer/graph.json` and `status.json` are gitignored — they can contain a
  user's source. Only `visualizer/demo_graph.json` (Droste indexing itself) is
  public.

## Good first issues

- New language extractor / edge rules in `core/treesitter_extract.py`.
- More cross-language bridges in `core/droste_ingester.py`
  (`_build_dependency_links`) — e.g. ORM table refs, GraphQL, gRPC.
- Visualizer polish in `visualizer/cockpit.html`.

## PRs

Small, focused, with a one-line rationale and a test. CI runs `pytest` on
Linux across Python 3.10–3.12.
