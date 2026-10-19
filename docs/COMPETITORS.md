# Code intelligence ecosystem — 19 October 2026

This is a source-based landscape review, not a performance benchmark. Stars
are a dated discovery signal, not active-user counts. Repository creation dates
do not necessarily indicate when a product first launched.

## New repositories during the last approximately 100 days

GitHub repository metadata was checked on 19 October 2026 for repositories
created since 11 July 2026. This is a targeted search, not an exhaustive census.

| Project | Repository created | Stars at check | Stated focus |
| --- | --- | ---: | --- |
| [Graphicle](https://github.com/Graphicle-AI/graphicle) | 14 October 2026 | 308 | Local Rust code graph plus docs, decisions and agent-session memory; CLI, UI and MCP. |
| [AxiomCodeGraph](https://github.com/AxiomCodeAI/axiomcodegraph) | 22 August 2026 | 212 | Code knowledge graph positioned around formal methods and agent context. |
| [BGTS Context Engine](https://github.com/bgts-ai-org/bgts-context-engine) | 8 September 2026 | 75 | Deterministic graph retrieval with PostgreSQL, Apache AGE, pgvector, MCP and REST. |

## Established alternatives remain active

These projects predate the review window; they should not be described as new
entrants merely because they published a recent release.

| Project | Stars at check | Latest GitHub release observed | Relevance |
| --- | ---: | --- | --- |
| [GitNexus](https://github.com/abhigyanpatwari/GitNexus) | 47,946 | [v1.6.12, 12 September](https://github.com/abhigyanpatwari/GitNexus/releases/tag/v1.6.12) | Local code graph, impact analysis, MCP and visualization. |
| [codebase-memory-mcp](https://github.com/DeusData/codebase-memory-mcp) | 46,900 | [v0.12.1, 16 October](https://github.com/DeusData/codebase-memory-mcp/releases/tag/v0.12.1) | Native binary with broad language support and persistent graph queries. |
| [Serena](https://github.com/oraios/serena) | 30,379 | [v2.0.0, 13 October](https://github.com/oraios/serena/releases/tag/v2.0.0) | Semantic code navigation and editing through language tooling. |
| [CodeGraphContext](https://github.com/CodeGraphContext/CodeGraphContext) | 4,297 | [v0.5.7, 8 August](https://github.com/CodeGraphContext/CodeGraphContext/releases/tag/v0.5.7) | CLI and MCP interfaces to a code graph database. |

“Latest GitHub release” is the API's latest stable release, not necessarily the
latest package version or default-branch feature set. Competitor performance
and language-count claims were not independently reproduced.

## Implications for Droste

Graph retrieval, local execution and MCP support are now common features.
Droste should not claim exclusivity or product-level superiority from its
self-supervised graph-neighbor benchmark.

The practical direction is a reliable Python tool with explicit token budgets,
clear source provenance, understandable cross-language links and an accessible
visual map. The next validation work should compare fixed public repositories
and actual coding tasks, measuring precision, missed edges, setup time, index
latency, memory and answer quality at the same context budget.

Current limits: heuristic symbol resolution, a static viewer snapshot, and
single-writer sharded persistence. The optional reactive dashboard currently
watches Python additions/modifications; full re-indexing is needed for removals.
