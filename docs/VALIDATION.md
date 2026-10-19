# Validation and intended use — 1.3.0

Droste is an alpha tool for finding definitions, inspecting static references,
and preparing source context. It is useful when a developer or agent needs a
small, traceable view of an indexed repository. Verify important conclusions
against source files and project tests.

## Verified workflows

- The deterministic regression suite has 105 cases, including progressive token budgets, retained views, scope/import
  resolution, ambiguous names, stored evidence, root isolation, index freshness,
  ignore rules, backend changes, deletions, renames and bounded traversal.
- CI runs Python 3.10/3.11/3.12 on Linux and Python 3.12 on Windows.
- A built wheel is installed outside the checkout. Its smoke test exercises
  index, status, context, check, find, references, zoom without opening an editor,
  the HTTP viewer, and actual stdio calls to all ten packaged MCP tools.
  Token vocabularies are prepared once before the offline smoke runs.
- Regression and installed-wheel tests use the offline hash backend. They do
  not validate the ranking quality of the real embedding model.
- A local installed-wheel check also verified switching from hash mode to real
  fastembed, distinct 384-dimensional vectors and correct cache invalidation.
  This verifies operation, not semantic ranking quality.
- A local CLI index of Droste's own source at `b6b4217` included 83 files and
  756 symbols with no coverage cap reached. SHA-256 freshness, the two source
  callers of `_build_dependency_links`, and a 2,446-character context under a
  2,500-character budget were verified. This is a workflow check, not a speed test.
- A live installed `watch` process was checked while a Python file was deleted
  and a TypeScript file added; the reloaded graph reflected both changes and
  reported fresh source hashes.

## Independent public source assertions

Run `python eval/public_corpus.py --output public-results.json`. It downloads
source files for parsing only; downloaded Python is never imported or executed.
The commits are fixed and the report records each source SHA-256:

- [Requests b25c87d](https://github.com/psf/requests/tree/b25c87d7cb8d6a18a37fa12442b5f883f9e41741):
  `src/requests/api.py` and `src/requests/sessions.py`.
- [CPython 0cc8128](https://github.com/python/cpython/tree/0cc81280367df838c4b199f8f0378837165071c2):
  `Lib/urllib/parse.py`.

The 20 manually labelled assertions include 16 expected direct relationships
and four forbidden relationships. [The complete result](validation-1.2.0.json)
records **15/16 expected edges found and 0/4 forbidden edges emitted** (19/20
assertions pass). The known missing edge is `requests.api.request` to
`requests.sessions.Session.request`: its receiver is an instance introduced by
a `with` statement. Droste currently resolves the constructor but does not
infer that receiver's type. `--strict` makes this documented miss fail the run;
the normal CI command fails on unexpected regressions and records the known gap.

These are selected edge assertions on three files, not exhaustive precision or
recall for the projects. They do not measure semantic search, agent task success,
runtime performance, or superiority over Serena, GitNexus or other products.
The older self-supervised graph-neighbor evaluation remains a retrieval diagnostic.

## Practical use on Requests

[The practical review](PRACTICAL_REVIEW.md) records real CLI investigations of
redirect authentication, timeout conversion and environment proxies on the full
pinned Requests repository. Exact-symbol navigation helped inspect the relevant
functions and their references. Open-ended English queries often returned tests
or broad contracts; the Italian query missed the relevant function. These
retrieval weaknesses remain, as does the incomplete dynamic request trace.
Two issues found during this review (symbol priority and sliced source locators)
are fixed in 1.2.1. This is an author-run case study, not an independent agent
benchmark or evidence that Droste beats ordinary search or competing products.

## Operational guarantees and limits

The new [exploration interface](EXPLORATION.md) uses a complete-response token
budget and three explicit levels of detail. Its receipts only acknowledge
content retained by the client. Its precomputed Requests demo reports payload
sizes, not model task success or API billing. The character-budget statements
below describe the legacy `context` interface.

- Python links resolve lexical scopes, imports/re-exports, aliases and explicit
  `self`/`cls` members. Unknown receiver types, dynamic dispatch, import-star and
  inherited member lookup may be missed. The parser never executes the project.
- Other languages use local/unique-name heuristics. Ambiguous name matches are
  omitted. DB/string bridges are heuristic; their resolution method is exposed.
  No reference result proves that code is dead or that a change is safe.
- `find` returns symbol IDs and exact source locations. IDs belong to the current
  index; re-find a symbol after edits that move its definition.
- `references` refuses to select an ambiguous name, limits traversal to five
  hops and at most 500 edges, and includes up to 20 evidence sites per edge
  with the full site count. It reports truncated results.
- `check` verifies file SHA-256 hashes and discovers additions/deletions. Context,
  status and reference queries use a cheaper size/mtime check and report warnings.
  A fresh index means the recorded inputs match, not that static analysis is complete.
  File/symbol/size caps report partial coverage. Raise the relevant `index` limits.
- Nested `.gitignore` patterns and root `.drosteignore` patterns filter files,
  including tracked files matching those patterns. Parent ignore files outside
  the selected root and Git's global excludes are not loaded. Hard exclusions
  for dependencies/generated storage and symlinks cannot be negated.
- `watch` polls and batches root refreshes using cached nodes and embeddings.
  It rebuilds all links after additions, edits, deletions and renames. Polling
  checks size/mtime; use `check` to detect edits that preserve both. This is not
  a constant-time incremental indexer. `view` is a snapshot; reopen it to refresh.
- One writer per database. File replacements are atomic; the multi-file store
  is not a transaction. Back up the metadata and its `.droste` directory together.
  Re-index after an interrupted write. `index --reset` reparses the selected root
  and preserves other roots and manually injected concepts.
- Context budgets are **characters**, minimum 500, including separators in
  `compiled_context`. Large bodies are represented by contracts or explicit
  source pointers. JSON metadata, warnings and actual model token counts are
  outside that character budget.
- Embedding caches are model-specific. Switching backends requires a re-index;
  until then context uses lexical search and warns. Hash mode provides no neural
  semantic search. The default real model may need a first-use download.

The packaged `droste mcp` server is the supported interface for this release.
The older repo-local custom plugin adapter is a separate legacy interface and
does not expose the new inspection tools; use the packaged server for this workflow.
