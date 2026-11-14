# Repository and global knowledge handoff

This is the **planned B/C integration contract**, not a claim that these new objects or routes already exist. Reuse existing typed provider, retrieval and intake boundaries. Agree concrete schema changes with the current integration captain before parallel implementation.

## Responsibilities and storage

| Object | Owner | Storage / trust boundary |
| --- | --- | --- |
| Private repository profile | B | Local immutable snapshot/evidence; no automatic upload of code, dependency lists or decisions to a public pipeline. |
| Public global entry | C | Public-source processing in Databricks; curated runtime catalogue in separate Atlas collections/indexes. This pipeline is new work. |
| Applicability match | B | Computed locally from the two objects and passed to existing repository signal intake. |
| Agent context and edits | A | Bounded source-linked evidence, matched public facts and scoped approved decisions through existing model schemas/worker. |

Existing Atlas `evidence_vectors` contains source-derived vectors and identifiers scoped to repository/snapshot/embedding space, with local source hydration. Keep it separate from proposed public `global_entries`/revision collections and their access policy. These new collection names are proposals, not current config variables or provisioned resources. Even embeddings and dependency lists can reveal private information. Remote embedding services receive the text they embed only when explicitly configured for that transfer.

SQLite remains authoritative for workflow state, repository knowledge approval, leases, checks and patch approval. Team notes stay outside model inputs, retrieval, training and cloud analytics. `backend/knowledge.py`'s `global` scope is repository-wide, not shared global knowledge.

## Minimum payloads

**RepositoryProfile (B):** repository ID; immutable source snapshot ID and index identity; exact resolved ecosystem/package/version with manifest/lockfile path, content hash and source range; framework/feature evidence; supported capabilities and gaps. Reuse `signals.dependencies(index)` for its supported packages/gaps, but add snapshot and source binding: its current return value alone is not this profile. Unsupported lockfiles, unresolved ranges and missing evidence stay unknown.

**GlobalEntry (C):** stable canonical ID and source aliases; kind (`vulnerability`, `improvement`, `framework_update`, `research_update`); original primary source URL; published/retrieved timestamps; source content hash and material revision; affected ecosystem/package/version rules and known fixed versions; conditions, evidence and limitations; freshness, withdrawal and source-quality status. A retrieval timestamp alone must not create a new material revision. Source-quality review never approves a repository patch or promotes a team policy.

**ApplicabilityMatch (B):** repository/profile snapshot identity; global entry ID/revision/hash; `matched`, `not_matched` or `unknown`; matched package/version or framework/code evidence; source citations; repository-specific rationale; proposed acceptance criteria; limitations. Version matches establish affected-version applicability, not exploitability. Similarity search can suggest candidates, not confirm bugs or improvements. Performance proposals need measured before/after checks before claiming a gain.

A receives a bounded context bundle with actual file ranges/hashes, current match provenance, missing evidence and relevant approved decision versions. Public articles and repository text are untrusted evidence, never permission changes. Revision comments remain job-local unless separately promoted and approved as knowledge.

## Databricks to Atlas pipeline (C, planned)

1. Fetch bounded public OSV/curated source records with attribution and retrieval time. Reuse the application's public URL validation; reject private addresses and unsafe redirects. Preserve source failures and partial coverage.
2. Normalize ecosystems, aliases, affected/fixed version rules and evidence; validate the agreed schema. Retain source revisions and withdrawal status. Unsupported versions remain unknown.
3. Deduplicate by canonical source identity and meaningful content, retaining aliases and original provenance. Produce versioned curated records; expose stale/failed ingestion instead of a fabricated clean scan.
4. Publish validated revisions to the separate Atlas catalogue idempotently, marking a revision available only after its complete validated content is stored. Retry publication without duplicating entries or losing the previous valid revision.
5. Offer bounded package/ecosystem queries for B. Runtime repository matching reads the published catalogue; it does not need to start a Databricks pipeline for every request. Label stale/offline results.

Start with OSV plus one sourced improvement. Existing `scripts/integrations.py` has private-vector commands and agent-metrics export only; these catalogue stages and their commands still need implementation. Existing terminal metrics remain a separate Databricks evaluation dataset with no source, prompts, notes or logs.

## Existing intake and lifecycle

Map a matched vulnerability to existing `source=security`, an improvement to `analysis`, and framework/research material to `research`; select the existing appropriate job type (`fix`, `security`, `refactor`, `performance`, `feature`). Catalogue kinds are not new accepted `JobInput.source` enum values. Use existing `POST /api/intake` or its shared service; do not bypass preparation policies or exact-patch review.

C deduplicates public source records. B deduplicates repository proposals using repository identity, canonical finding, affected package/version and meaningful evidence revision. Keep unchanged source keys stable and revalidate the current snapshot before intake. Preserve rejection/snooze decisions for unchanged evidence; a polling timestamp must not reopen a job.

Current intake fingerprints title/description/source URL/type. It does **not** yet bind the proposed catalogue revision fields. B and the captain must add explicit match/revision bindings and invalidation through the existing workflow before claiming catalogue freshness is enforced. When source, applicable version, catalogue revision, approved knowledge or decisions change, re-assess applicability and invalidate dependent plans/checks/approvals before reuse. Never silently replace evidence attached to an existing patch. Withdrawn/stale entries must be labelled and withheld from fresh automatic proposals until revalidated.

## Independent development and acceptance

C supplies small labelled public catalogue fixtures in the agreed shape; B supplies a private sample profile plus expected match/non-match/unknown cases; A verifies the actual evidence/approved decisions reaching its provider. These fixtures let each lane progress without claiming live cloud execution. No private profile is a global fixture.

The shared acceptance journey must prove: exact-version match with citations; unrelated repository rejected; unsupported version unknown; unchanged rescan without duplicate; materially changed/withdrawn record invalidates applicability; deleted source is not current evidence; scoped decisions reach the model; team notes never do; a resulting patch has actual checks and developer review. Record live Databricks ingestion/publication and Atlas query evidence separately when available.
