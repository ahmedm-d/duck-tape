# Duck Tape

Duck Tape is a local, AI-assisted code-maintenance workspace. It understands a repository, turns maintenance signals into persistent jobs, prepares isolated Git patches, and gives developers a diff with evidence to review.

The goal is to reduce the work between noticing a problem and having a checked patch ready for review. Tickets, explicit requests, dependency advisories, repository findings and research can all become maintenance proposals. Repository-wide context and approved team decisions should inform each change, rather than treating an individual file as the whole project.

**Steward was the working name during development.** The application UI, Python configuration variables (`STEWARD_*`) and some documentation still use that name. This repository is called **Duck Tape**; those existing identifiers remain compatible with local installations.

## Three-person hackathon workspace

Start with the [team plan](docs/hackathon/TEAM_PLAN.md), [three copy-paste kickoff prompts](docs/hackathon/START_HERE.md) and [repository rules](CONTRIBUTING.md). The workstreams are **agent intelligence**, **repository memory/evaluation**, and **product experience/integration**. File ownership is defined in [AGENTS.md](AGENTS.md). The [foundation verification](docs/evidence/hackathon-foundation/README.md) records current checks and remaining live setup. Fetch the latest `main` before starting the parallel workstreams; the original initial commit is preserved.

The [internal stack foundation](docs/integrations/README.md) uses **Hugging Face and Adaption for Duck Tape's agent inference**, **MongoDB Atlas for its evidence memory and retrieval**, and **Databricks Data Intelligence Platform for its agent-run evaluation**. These power Duck Tape itself; they are never installed into imported repositories. Model adapters are connected to the existing worker. Atlas projection/search and Databricks metrics export are implemented as explicit operator commands in `scripts/integrations.py`; their automatic worker wiring is the data owner's next milestone. SQLite remains the transactional workflow authority and local fallback. Live accounts, provisioned resources and model qualification still need the acceptance steps in the team plan.

**Submission provenance:** the supplied event rules require code written during the hackathon and a public repository. This project contains existing implementation, and its history was consolidated before this work. The new Git history is not proof of when the code was written. Confirm eligibility and disclose existing work before submitting; see the [submission checklist](docs/hackathon/SUBMISSION.md). This foundation does not change repository visibility.

## How it works

1. Import a public HTTPS Git repository or ZIP, or open the bundled Parcel sample.
2. Inspect its immutable snapshot, source inventory, symbols, dependencies, tests and evidence-linked context. Python and JavaScript/TypeScript have parser-backed relationships; other languages have labelled text-search support.
3. Create a request or scan for maintenance signals. Each repository and source type can require preparation approval or start preparation automatically.
4. A separate persistent worker retrieves context, plans the change, asks for meaningful product decisions, applies structured edits in isolation and records verification results.
5. Review the versioned diff, checks, uncertainty and discussion. Team notes stay outside model prompts; revision requests produce a new patch and invalidate old approval.
6. Approve that exact revision, export it, or apply it to a managed local integration branch. Nothing automatically merges into an imported repository's main branch or pushes to a remote.

Repository text and external material are evidence, not permission to run commands or reveal secrets. Arbitrary imported or model-generated code requires a constrained sandbox. A Git worktree by itself is not a sandbox.

## Current status

This is a functioning **local prototype**, with a real backend, worker, persistence, Git operations and verification. It is not a production multi-user service or a claim that a small model can safely maintain arbitrary repositories unattended.

| Area | What is available |
| --- | --- |
| Sample workflow | Four explicitly labelled fixture scenarios: cross-module bug, feature decision, dependency advisory and measurable performance proposal. Edits, diffs, checks, revisions and managed application are real. |
| Repository understanding | Snapshot-isolated indexing, Python/JS/TS parsing, full-text and graph retrieval, source explorer, scoped knowledge, optional embeddings and evidence-linked Q&A. Missing synthesis and coverage gaps are shown. |
| Live inference | Adaption Responses, Hugging Face Inference Providers, OpenAI-compatible and Ollama adapters; typed responses, routing, budgets, telemetry and a separate review pass. No proprietary inference account is required. |
| Live model reliability | The recorded 3B baseline passed two cases, failed eight model-quality cases and left two cases incomplete after runtime interruption. It is **not qualified**. Later schema/startup fixes have deterministic tests; a fresh live qualification is still required. |
| Verification | Backend, browser, restart and actual Docker evidence are retained. Fixture inference, live inference and externally incomplete runs are distinguished. The new paired retrieval-to-task harness still needs its live run. |

See [verification and limitations](docs/VERIFICATION.md), the [14-journey acceptance mapping](docs/evidence/acceptance-traceability.md), and the [recorded model baseline](docs/evidence/model-qualification-baseline/README.md). Passing a health check or generating a valid response does not establish coding quality.

## Architecture

| Component | Implementation |
| --- | --- |
| Frontend | React, TypeScript and Vite; light/dark themes, source explorer and patch review |
| API | FastAPI/Python with validated inputs and guarded workflow transitions |
| Persistence | SQLite for repositories, snapshots, graph/index records, knowledge, jobs, revisions, checks and audit events |
| Execution | Separate supervised worker, durable work items, leases, checkpoints and cancellation |
| Agent inference | Hugging Face open-weight inference and Adaption Responses; server-side routing, validation and a separate review pass |
| Repository memory | MongoDB Atlas vector projection with repository/snapshot filters and local source evidence; SQLite local fallback |
| Agent evaluation | Databricks SQL export of measured terminal-run outcomes, verification and usage; unknown values remain unknown |
| Verification sandbox | Constrained Linux Docker containers, bounded resources, network disabled during checks and controlled dependency preparation |

Source lives in `backend/`, `frontend/`, `scripts/`, `sandbox/` and `tests/`. Runtime data, credentials, installed dependencies and model weights are excluded from Git. The architecture and configuration guides below describe the boundaries in more detail.

## Start

Prerequisites: Python 3.13+, Node 22.12+ and Git. From this directory:

```powershell
python scripts/setup.py
.venv/Scripts/python run.py
```

Open **http://127.0.0.1:8765**. On macOS/Linux use `.venv/bin/python`. One startup command supervises the API and a separate persistent worker. The initial sample is seeded once; existing data is preserved. Persistent data defaults to `data/workspace/`. Model weights and machine-specific runtime manifests are not included in a clone.

For sandbox execution, install a working Linux Docker runtime and build the pinned images with `python scripts/setup.py --sandbox`. The optional setup command performs no privileged installation. If Docker is unavailable, repository analysis and patch export still work; only the reviewed sample fixtures can execute through the restricted host fallback.

Configure an open-weight Ollama or OpenAI-compatible endpoint in Settings or the project's `.env` (see [.env.example](.env.example)). Credentials belong only in the server environment. No proprietary model, account or paid service is required. Run `.venv/Scripts/python scripts/doctor.py` to inspect actual local capabilities.

## Demo walkthrough

1. Open the preloaded **Parcel · sample**. Repository shows snapshot identity, inventory, parsed relationships and source citations. Search for quote behavior to trace the endpoint, pricing helper, configuration and tests.
2. Open **Quantity is ignored when calculating a quote**. Approve preparation. The fixture provider produces a real patch and executes baseline, failing-before regression and passing-after tests.
3. Inspect the diff, explanation and checks. Add a **Team note**; it never enters model context. **Ask AI to revise** with “add zero/default quantity regression coverage” exercises the documented sample revision. Old diffs remain and old patch approval is invalidated.
4. Approve the current revision. Export its patch or changed files. **Apply to managed branch** creates a real local integration commit and records Completed. The source checkout stays unchanged.
5. Prepare the oversized-order feature and answer its decision card. Run the performance proposal for actual comparable benchmark measurements.
6. Scan twice: the labelled sample dependency advisory creates one sourced job. Source policies and monitoring controls determine whether later signals wait for preparation approval.
7. Edit and approve scoped Knowledge, then prepare another job to inspect the included policy version. Import a ZIP or public HTTPS Git repository to use the generic analysis and provider pipeline.

**Create fresh sample** makes another isolated workspace. Sample outputs apply only to the four documented fixture scenarios, not arbitrary requests. Live model requests use the generic provider adapter. Unsupported environments and failed verification remain visible blockers.

## Documentation

- [Architecture and trust boundaries](docs/ARCHITECTURE.md)
- [Configuration, supported environments and intake API](docs/CONFIGURATION.md)
- [Verification evidence and capability status](docs/VERIFICATION.md)
- [Acceptance matrix and reproducible release tiers](docs/ACCEPTANCE_MATRIX.md)
- [Offline backup, restore and artifact cleanup](docs/MAINTENANCE.md)
- [Repository understanding and retrieval measurements](docs/RETRIEVAL.md)
- [Implementation checklist and decisions](docs/PLAN.md)

The UI uses clearly labelled demo members. It is a single-user loopback prototype, not production authentication. Secret exclusion is conservative and heuristic. Model availability and valid citations do not guarantee correct code; inspect the patch and evidence.

## Checks

```powershell
.venv/Scripts/python -m pytest -m "not live" -q
cd frontend
npm run build
npx playwright install chromium
npm run test:browser
cd ..
.venv/Scripts/python scripts/check_restart.py
```

Live Docker/provider tests are opt-in under `tests/live/`; their environment switches and recorded outcomes are documented in the verification report. Browser fixture contracts and actual backend journeys are recorded separately. The developer workspace also supports an existing `vendor-current/` dependency directory; it is excluded from source control.

For one machine-readable release check, run `.venv/Scripts/python scripts/acceptance.py --tier deterministic`. The separate `sandbox` and `live` tiers require their actual services and report unavailable prerequisites as blockers. Browser checks need `npx playwright install chromium` or `STEWARD_BROWSER_EXECUTABLE` pointing to an installed Chrome/Chromium executable.

This repository begins with a consolidated initial commit. Earlier build and evaluation notes are preserved for context; any older commit IDs in those artifacts refer to the development history before this reset, not commits available in this repository.
