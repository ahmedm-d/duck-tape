# Repository understanding and retrieval

Python AST and JavaScript/TypeScript/TSX Tree-sitter parsing extract scoped symbols and source ranges. Static resolution includes module imports, supported aliases/workspaces, re-exports, routes, configuration and tests. Parsed/resolved and inferred edges are labelled. Dynamic dispatch is incomplete; other languages get honest text search capabilities.

The inventory explicitly records indexed, excluded, unsupported and failed entries with reasons. Generated directories are counted as excluded directory entries, not invented child counts. Eligible UTF-8 source is chunked across the entire file. Immutable source blobs and the search graph are separate so editing and historical evidence do not depend on truncated retrieval.

Persisted full-text chunks are ranked with exact path/symbol matches, optional embedding similarity and graph neighborhoods. Nonadjacent ranges in the same file are preserved; overlapping ranges are deduplicated. Approved scoped knowledge is reserved before source selection. The context budget uses a conservative UTF-8 byte upper bound and reports a blocker rather than silently dropping required policy. Every item records repository, snapshot, hash, path, lines, chunk and retrieval reason.

With a real embedding model configured, all eligible chunks participate in batches of 32; there is no first-64-files or file-prefix cutoff. Vectors are persistent and namespace/model/dimension checked. A source line larger than 12 KiB has an explicit chunk capacity gap; immutable raw source remains available. Provider failure exposes keyword/graph fallback. Team notes never enter retrieval, embeddings or synthesis.

Module, package and repository summaries have validated source references. Each summary build permits at most 12 model calls; other scopes use labelled structural facts. Missing/invalid citations produce structural fallback. Q&A validates citations against the actual supplied ranges. A valid reference proves a source location, not that a model's interpretation is correct.

## Evaluation

Run `python scripts/evaluate_retrieval.py`. Relevant files and ranges are specified independently for fixed Python and TypeScript tasks. Results record recall, precision, context size, actual latency and unavailable measurements honestly.

The completion evaluation found all required files and ranges on four graph tasks. File precision was 0.50, 0.33, 0.43 and 0.57; contextual expansion also adds non-label files. Some keyword tasks already had full recall. Cold initialization and warm queries are mixed, so no latency improvement is claimed. The exact measurements are retained in the completion evidence folder.

Actual live Q&A passed with a resolving `helper.py:1-2` citation and reported usage of 968 input / 69 output tokens. Latency was 127.876 seconds including model queue contention. Repository and module summaries subsequently passed live inference with validated source references ([summary evidence](evidence/live-summaries.json)).

The separately installed Nomic Embed v1.5 Q8 model produced real 768-dimensional vectors, persisted per repository/snapshot with reported token usage and measured latency ([embedding evidence](evidence/live-embeddings.json)). The [live semantic comparison](evidence/retrieval-semantic-live.json) found all labelled files/ranges on the same four tasks. Graph retrieval already achieved that recall, and semantic retrieval was slower; these measurements do not establish a semantic quality or performance win. Each evaluation uses a fresh vector namespace, then reuses document vectors across its later queries. Unknown usage remains null.

ZIP and public HTTPS Git imports do not execute source. GitHub snapshots use the bounded pinned-commit archive path; other HTTPS hosts use a restricted bare Git fetch with exact raw blob export and bounded history. Submodules are reported as gaps. Refresh replaces the current index/edges and preserves historical snapshot access; deleted files cannot return as current evidence.

## Atlas candidates (opt-in)

With `STEWARD_ATLAS_ENABLED=1` and `STEWARD_ATLAS_RETRIEVAL=1` (or `retrieve(..., atlas=True)`), generic retrieval adds `data_platforms.atlas_search` results as extra scored candidates. Only IDs that resolve to locally eligible chunks of the same repository/snapshot (and any `paths` filter) count; content always comes from local evidence. Results show `+ atlas` in `mode` and an `Atlas candidates: N validated` note. Any Atlas failure leaves keyword/graph retrieval in place with an `Atlas candidates unavailable (...)` note, never an Atlas success label. The query embedding is sent to Atlas; the query is truncated to 12 KiB and credential-like queries are refused by the adapter.

Live check, 14 November 2026, Parcel sample (14 chunks, Ollama `nomic-embed-text`, 768 dimensions, Atlas M0), keyword+graph versus keyword+Atlas+graph, labelled relevant files found: at limit 3, quantity bug 3/4 versus 2/4, oversized orders 0/3 versus 0/3, search performance 1/1 versus 1/1; at limit 6, 4/4 versus 4/4, 1/3 versus 2/3, 1/1 versus 1/1. Three queries on one small fixture do not establish a retrieval gain, so Atlas candidates stay disabled by default.
