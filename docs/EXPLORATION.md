# Progressive space, bounded tokens — 1.3.0

Droste's new `explore` interface turns a graph region into a small view that
can be expanded from **map → structure → source**. Find a concept, navigate by
the returned node IDs, and spend a chosen token budget on the detail you need.

The graph can grow beyond a model's context. Storage, indexing and analysis
are finite; “expanding space” does not mean infinite RAM or a constant-time
query on arbitrarily large repositories. This first version still loads graph
metadata and scans source hashes on each exploration request.

## CLI

```bash
python -m pip install --upgrade droste-memory
droste index .
droste explore --tokens 1000
droste explore "timeout" --tokens 1500
```

The response contains node IDs in `items`. Pass one as `--focus` to keep the
same region while changing `--level map`, `--level structure` or `--level source`.
A unique, exact qualified symbol also works. On the pinned Requests example:

```bash
droste explore --focus HTTPAdapter.send --level map --tokens 1000
droste explore --focus HTTPAdapter.send --level structure --tokens 1000
droste explore --focus HTTPAdapter.send --level source --tokens 1500
```

Map items contain names and source locations. Structure items add signatures,
the first docstring line and, for classes, up to 12 method signatures. Python
file structures list up to 20 top-level definitions rather than selecting only
the module's first function. Source
items contain whole numbered source spans. When a span does not fit, the item
downgrades to structure or map; its `level` records what was actually returned.
Code is never sliced mid-line to satisfy the budget. Neighbors use structure
or map while the focus gets first choice of the source budget.

The response is one compact JSON text, not a pretty-printed envelope plus
another copy of the content. `used` counts **the entire returned text**, including
metadata, locators, receipts, JSON escaping and the final newline. Limits are
256–64,000 tokens. Select `--encoding o200k_base` (default) or `cl100k_base`.
These are reference tokenizers, not a guarantee for every model's tokenizer.
Provider chat wrappers, tool definitions, tool-call arguments, other messages
and output tokens are outside this response budget. Counts are not API bills.
Formatting or transforming the response in another client can change its count.

Two small vocabularies need a first-use download. Prepare them once with
`droste explore --prepare-tokenizers`; cached vocabularies work offline. The
semantic embedding model has its separate existing cache/download requirements.
No hosted model or API key is used to assemble exploration responses.

## Retained views and repeat visits

```bash
droste explore --focus HTTPAdapter.send --level source --tokens 2500 --save-receipt .droste/retained.json
droste explore --focus HTTPAdapter.send --level source --tokens 2500 --seen .droste/retained.json --save-receipt .droste/retained.json
```

Keep receipt files in the excluded `.droste/` directory, so they do not become
new source files and invalidate the index. A receipt contains representation
handles, not source bodies. Reuse requires the same root, node, source-file
hash and exact representation. A retained map never substitutes for source.
Changed files require re-indexing; old handles cannot acknowledge new source.

**Only pass receipts for content the client still retains.** Droste does not
know what a model remembers or whether it attended to that content. Omit
`--seen` after compaction, eviction, a new session or loss of the earlier text.
Start a new receipt file then. The server stores no session acknowledgements.
At most 1,000 handles can be acknowledged per request.

Reused representations appear in `reused` without their bodies. A revisit can
use the freed budget for previously omitted neighbors. Reuse therefore does
not guarantee a smaller response on every request. For an end-to-end token
comparison, count acknowledgement arguments as well as returned text.

## MCP

`droste_explore` exposes `query`, `tokens`, `level`, `focus`, `root`, `encoding`
and `seen`. It returns **one text block** containing the exact counted JSON.
The server does not also attach a duplicate structured result. Agents can read
the map, choose a focus ID, zoom in, and explicitly acknowledge retained handles.
The other nine tools, including the legacy character-budget `droste_get_context`,
remain available. The older custom plugin adapter does not expose this new tool.

## Freshness, omission and references

Exploration checks source SHA-256 hashes. A stale or unverifiable index returns
`needs_index` without source or reused content. A second hash check on files
read for the response catches intervening edits. This is not a transactional
filesystem snapshot; concurrent writers can still race after a read. Use a
stable checkout for reproducible investigations.

`omitted` counts candidates not represented in the response, **not every
unseen symbol in the repository**. Candidate processing is capped at 200 and
reports that cap. `links` index into the new `items` array; they describe static
references, including inheritance. `links_truncated` reports a budget cut among
eligible new-item links. Use `references` for call-site evidence and for relations
involving reused items. Dynamic calls and unresolved receivers can be absent.

Ambiguous focus names return candidate maps rather than an arbitrary source.
`budget_too_small` means even a map of the focus did not fit. A partial index is
marked and warned. Natural-language ranking retains the weaknesses observed in
the [practical review](PRACTICAL_REVIEW.md); exact focus navigation is more reliable.

## Public demonstration and observations

[Open the interactive demo](https://lorismascio17.github.io/droste-memory/explore.html).
It switches real, precomputed exploration responses for three functions in
[Requests b25c87d](https://github.com/psf/requests/tree/b25c87d7cb8d6a18a37fa12442b5f883f9e41741).
Wheel/scroll zoom changes representation as well as the view. Token and budget
controls reveal what each response actually contains. This page makes no model
calls. The CLI/MCP work on the local repository; the public page is a recorded demo.

| Focus | First source response | Retained revisit response |
| --- | ---: | ---: |
| `HTTPAdapter.send` | 2,500 tokens | 827 tokens |
| `SessionRedirectMixin.rebuild_auth` | 776 tokens | 266 tokens |
| `should_bypass_proxies` | 2,346 tokens | 676 tokens |

Counts use `o200k_base` and a 2,500-token response budget. The revisit acknowledges
the representations returned in that first response; new neighbors may also
fit. [The complete recorded text](spatial-demo.json) includes separate argument
token counts. These are selected payload-size observations. They do not prove
better task outcomes, latency, billing savings or superiority over other tools.
Whole-file comparisons in the demo are explicitly size comparisons; direct
symbol reads can be smaller than either a whole file or a graph response.

The generator verifies all indexed inputs against the pinned public archive
before exporting snippets; the upstream Apache 2.0 license accompanies the demo.
To reproduce with that tree already indexed:

```bash
python eval/spatial_demo.py --root path/to/requests --db path/to/requests-index.json --output .tmp/spatial-demo.json
```

Validation includes 105 local regression cases, exact counts under both encodings,
all three detail levels, Unicode/special-token literals, retained-source and
compaction reset behavior, ambiguous/root-scoped navigation, stale hashes and
edits during a request. Installed-wheel smoke checks the CLI receipt workflow
and actual stdio output from all ten MCP tools.
