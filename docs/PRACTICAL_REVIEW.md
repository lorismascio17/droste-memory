# Practical investigation review — 19 October 2026

Droste helped inspect known functions and their static references. It did **not**
reliably discover implementation details from open-ended questions in this trial.
Use the symbol tools and verify source before relying on a change-impact result.

This is an author-run case study on real source, not a blind or independent
agent benchmark. The author also implemented the fixes described below. There
was no competing-agent run, randomized task assignment or task-time comparison.

## Setup

- Full [Requests repository at b25c87d](https://github.com/psf/requests/tree/b25c87d7cb8d6a18a37fa12442b5f883f9e41741),
  including tests and supported documentation; downloaded source was never executed.
- Installed 1.2.0 CLI baseline, then the 1.2.1 source checkout against the same index.
- Real `fastembed:BAAI/bge-small-en-v1.5`, not the hash fallback.
- Index: 65 supported files, 778 symbols, 708 syntax dependencies; no coverage
  cap reached and a fresh SHA-256 check. Unresolved call sites still exist.
- Each context query received 6,000 **characters**, including separators.
  JSON metadata is additional. Each CLI call started a separate Python process.
- Exact queries, selected symbols, body-line observations and timings are in
  [the portable observation record](practical-review-2026-10-19.json).
- A subsequent run repeated all queries through the installed 1.2.1 wheel on
  a fresh index with real fastembed and the same focus results. The record
  includes this check, its local wheel checksum and its index time (66 seconds).

## What happened

| Investigation | Open-ended query | Follow-up with a symbol |
| --- | --- | --- |
| Where redirect credentials are removed | English query focused on a test retaining auth on the same host; it omitted `rebuild_auth`. Italian query focused on a test helper named `host` and also missed it. | `SessionRedirectMixin.rebuild_auth` returned its full body. `references` correctly connected `resolve_redirects` → `rebuild_auth` → `should_strip_auth`, with call-site evidence. |
| Where a connect/read timeout tuple is converted | Focused on `ConnectTimeout`; `HTTPAdapter.send` appeared only as a contract, without its conversion logic. | `HTTPAdapter.send timeout` incorrectly focused on `Timeout` in 1.2.0. After the fix it returned the full `send` body, including tuple unpacking and `TimeoutSauce(connect=connect, read=read)`. |
| How `NO_PROXY` affects environment proxies | Focused on `test_should_bypass_proxies_no_proxy`; the implementation appeared as a compact contract. Its matching logic was absent. | `should_bypass_proxies` returned the full matching logic. Caller lookup found `get_environ_proxies` and `resolve_proxies`, plus relevant tests and their upstream callers. |

These are concrete navigation successes after refining a query, not three
automatically scored task successes. Understanding all redirect exceptions also
requires inspecting `should_strip_auth`; a caller edge alone does not explain it.

Two additional checks mattered:

- `references send` reported four candidates instead of selecting an arbitrary
  method. Qualifying it as `HTTPAdapter.send` resolved the ambiguity.
- A five-hop trace from `api.get` reached `api.request`, `Session` and its mixin
  dependency, but missed `session.request` introduced by the context manager.
  It therefore could not reconstruct the complete route to the HTTP adapter.
  No empty reference result should be used as proof that a function is unused.

## Fixes prompted by actual use

1. Unique exact symbol IDs and qualified mentions now take priority over
   semantic lookalikes. Ambiguous and partial qualified names are not pinned.
   The timeout follow-up now retrieves the requested function first.
2. Compact neighbor context previously sliced a 250-character string, sometimes
   cutting off a file path before its line number. It now drops optional
   decoration or uses a whole source pointer. It never slices a locator.
3. Syntax context now says “references” rather than adding call parentheses to
   every dependency. A class's inheritance dependency is not a constructor call.

Three deterministic regressions cover symbol priority, ambiguity/partial names
and long source locators. The complete local suite passes 72 cases.
The first two fixes were rechecked on the real Requests index. The four
open-ended queries still have the weaknesses recorded above; they are not
presented as fixed.

## Comparison with direct search

An operator-selected `rg` search over `src/requests` also located the relevant
lines: the three patterns returned 3, 9 and 24 lines (245, 718 and 1,943
characters). That was sufficient to identify useful files for manual inspection.
The search patterns are recorded in the observation JSON. They were selected
after reading the tasks and source; this is not a fair blind agent comparison.

On this machine those searches took about 0.04 seconds. Fresh CLI context
processes took about 4–8 seconds, including Python/model startup and index load.
A persistent MCP session amortizes startup; its warmed latency was not measured
here. The initial index also has a cost. These observations give no basis for
claiming Droste saves time or tokens versus direct search on these tasks.

The useful part was a bounded source bundle with definitions, references and
evidence. Discovery ranking, excessive test/exception matches, English-only
default embeddings and dynamic receiver tracing remain substantial weaknesses.

## Reproduce

With the desired Droste wheel installed, choose a new scratch directory:

```bash
python eval/practical_queries.py --work-dir .tmp/practical-review --output .tmp/practical-review.json
```

Add `--checkout` to investigate the current checkout. The script verifies the
pinned archive checksum, indexes a separate database and records the real CLI
responses, character counts and elapsed times. It does not grade its own answers.
