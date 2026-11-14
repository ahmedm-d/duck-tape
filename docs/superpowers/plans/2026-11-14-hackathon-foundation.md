# Hackathon Foundation Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development. Independent tasks have exclusive files; the lead integrates and verifies.

**Goal:** Deliver a tested internal agent, memory and evaluation stack foundation plus clear rules and kickoff prompts for three parallel developers.
**Architecture:** Preserve the local transactional core. Extend provider contracts, add explicit data-platform adapters and a network-free operator status API/CLI.
**Tech Stack:** Existing FastAPI/Python, React/TypeScript, SQLite/httpx; optional PyMongo only.
**Spec:** ../specs/2026-11-14-hackathon-foundation-design.md

## Global constraints
No secrets in Git/browser; no implicit cloud upload; no workflow database migration; no production/authentication claims; preserve current source and old evidence. User has authorized independent implementation without routine questions.

## Review focus
- A configured service must not appear live-verified without an actual call.
- Provider-specific credentials must never be sent to unrelated endpoints or embeddings.
- Remote vector hits must resolve to the requested local repository/snapshot.
- Analytics must preserve failures and unknown token usage instead of manufacturing gains.
- Shared-file changes must not create overlapping ownership or silently alter local workflow.

## Task 1: Provider bridges (A)
Files: backend/providers.py, tests/test_sponsor_providers.py, docs/integrations/INFERENCE.md.
Consumes the existing ProviderRequest/model schema; produces the same ProviderResult with explicit protocol/enforcement telemetry.
- [x] Add failing envelope/auth/parser/health tests using mocked HTTP.
- [x] Add Adaption Responses and Hugging Face chat modes, official-origin credentials, explicit embedding boundary.
- [x] Run new tests and existing provider contracts; review evidence.

## Task 2: Explicit cloud projections (B)
Files: backend/data_platforms.py, tests/test_data_platforms.py, requirements-cloud.txt, integrations/**, docs/integrations/DATA_PLATFORMS.md.
Produces the six functions named in the spec; status returns atlas/databricks objects with enabled, configured, state and detail only.
- [x] Add failing privacy/isolation/idempotency/error/unknown-usage tests.
- [x] Implement optional PyMongo vector projection and parameterized Databricks terminal-run metrics.
- [x] Add real SQL/index setup artifacts and account configuration instructions.
- [x] Run data adapter tests; review evidence.

## Task 3: Team and product foundation (C/lead)
Files: AGENTS.md, CONTRIBUTING.md, .editorconfig, .githooks/commit-msg, scripts/check_commit_message.py, scripts/integrations.py, .github/**, backend/main.py, frontend/src/main.tsx, tests/test_hackathon_foundation.py, frontend/tests/settings.spec.ts, .env.example, README.md, docs/hackathon/**.
Consumes provider modes and data interfaces; produces network-free operator readiness API, explicit CLI, repeatable commit conventions and three kickoff prompts.
- [x] Test commit validation, CLI privacy/disabled dispatch and API modes before implementation.
- [x] Add governance, operator status/API/config and docs without mass formatting.
- [x] Run focused backend and browser checks, full deterministic acceptance and production build.
- [x] Independent review; fix substantive findings; deliver evidence without changing commits.

## Decision log / progress
- 2026-11-14: keep SQLite authoritative; internal cloud adapters remain explicitly configured/manual until live acceptance and worker wiring.
- 2026-11-14: native isolated branch chore/hackathon-foundation based on 1d094be; original checkout preserved.
- 2026-11-14: read-only review identifies live model reliability as the priority, with provenance unresolved.
- 2026-11-14: no sponsor accounts are assumed; no credentials copied from existing environment files.

- 2026-11-14: user clarified these four services are Duck Tape infrastructure, not offerings to imported repositories; removed the new sponsor status cards and retained operator-only readiness.
- 2026-11-14: preserve all commits unchanged; deliver uncommitted changes and evidence.

- 2026-11-14: integrated deterministic gate passed (541 backend, actual worker/API restart, build, 16 browser cases); final post-review contracts passed 112. Independent security and fresh handoff reviews addressed; live account/model gates remain explicitly outstanding.
- 2026-11-14: deliver verified files to the original checkout as uncommitted changes; preserve Git HEAD and historical evidence.
