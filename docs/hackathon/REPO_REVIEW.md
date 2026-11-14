# Repository review for the hackathon

Reviewed baseline: **initial commit 1d094be**, Duck Tape, on 14 November 2026. This is a read-only review of source and retained evidence, assuming a three-person team demonstrates locally on one machine. It is not fresh application execution, a complete security audit, proof of eligibility or a claim that all tests currently pass.

## What already exists

The [README](../../README.md) describes a functioning local prototype: FastAPI, React/TypeScript, SQLite transactions, a durable worker, immutable snapshots, parsed relationships, retrieval, versioned approved knowledge, structured edits, constrained execution, exact-revision review and managed Git integration. The sample uses labelled fixture inference with real files and checks.

Use these capabilities together: one real cross-module maintenance request, a regression failing before the patch and passing afterward, an approved decision carried into later work, and a seeded unsafe proposal whose actual review result is shown. A longer feature catalogue does not resolve live-model uncertainty.

## Internal-stack scope

The intended stack belongs to Duck Tape: Hugging Face/Adaption for its internal model inference/adaptation, MongoDB Atlas for its evidence memory/retrieval, and Databricks for evaluation of its own runs and measured improvements. Imported/client repositories only supply evidence and receive reviewed maintenance patches. Sponsor integrations, SDKs, credentials and setup flows are not installed into them.

This product decision does not establish that the stack is already configured, wired into every worker path, live-qualified or improving automatically. Review foundation capabilities and remaining work separately.

## Findings and evidence gaps

### 1. P1: the recorded small-model baseline is not qualified

**Evidence:** [Verification status](../VERIFICATION.md) and the [frozen model baseline](../evidence/model-qualification-baseline/README.md) record two passes, eight model-quality failures and two externally incomplete cases. Feature, revision, knowledge and unsafe-review cases failed. Later schema/runtime changes have deterministic coverage, but no fresh live requalification is claimed.

**Concrete situation:** using that CPU-only 3B model for an unscripted feature, revision or unsafe-review demo relies on capabilities that failed in retained trials. Interrupted cases also do not count as successes. This concerns the recorded model/configuration, not every possible model or the new adapters.

**Smallest response:** select a capable real endpoint and exercise the generic repair/review path, then relevant held-out cases. Reproduce using the documented qualification driver in the installed environment:

~~~text
python scripts/evaluate_models.py --driver scripts.qualification_driver:driver --output <new-evidence-path>
~~~

Preserve failures and runtime blockers. Health or valid JSON does not establish application qualification.

### 2. P1 for release claims: the latest aggregate was not rerun green

**Evidence:** [Verification status](../VERIFICATION.md) retains an initial-publication run with 442 backend passes and one outdated runner-fixture failure. The fixture was corrected and its entire four-test file passed separately. Restart, production build and 15 browser contract tests passed in the retained aggregate.

**Concrete situation:** claiming the current full backend suite passed would exceed that evidence. This is an evidence gap; the record does not prove the corrected application fails.

**Smallest response:** run fresh deterministic acceptance on the integrated submission commit in isolated test data and report the actual outcome. Keep older failures historical:

~~~text
python scripts/acceptance.py --tier deterministic
~~~

### 3. P2: retrieval and adaptation benefits need measurement

**Evidence:** [Verification status](../VERIFICATION.md) reports that live embeddings produced no recall gain over graph retrieval on four fixed tasks. The paired retrieval-to-task harness has deterministic coverage, but its live pair was unmeasured in the reviewed baseline.

**Concrete situation:** adding Atlas search or a model-adaptation API and claiming better repairs would be unsupported. A service request proves use, not improvement.

**Smallest response:** compare the same small held-out task set with recorded retrieval/model versions. Include failures, latency, known usage and unavailable runs. Separate adaptation training from evaluation. Report no improvement if that is what was measured.

## Boundaries for parallel work

- [providers.py](../../backend/providers.py) already provides bounded inference, per-role routing, schemas, attempts, latency and token telemetry. Qualify this path instead of adding an agent framework. The baseline OpenAI-compatible path sends strict JSON-schema requests; compatibility of the new adapters needs actual endpoint evidence.
- [retrieval.py](../../backend/retrieval.py) returns scoped source chunks, citation identity, retrieval mode, approved knowledge and a budget. Connect Duck Tape's internal Atlas memory here while validating returned IDs against local evidence. Preserve a visible local fallback.
- [db.py](../../backend/db.py) and [workflow.py](../../backend/workflow.py) hold transactional guards. Replacing persistence risks leases, approval freshness and crash recovery; keep it in SQLite.
- [worker.py](../../backend/worker.py) persists stage/provider attempts and check evidence. Databricks can evaluate Duck Tape's own bounded terminal metrics to guide improvements, without owning execution or automatically receiving data.
- [frontend/src/main.tsx](../../frontend/src/main.tsx) contains much of the UI in dense shared code. Give it one owner; avoid broad formatting/refactoring while integrating.
- The app is intentionally local with labelled demo authors, not production authentication. A local demo and recording fit the documented boundary; public multi-user hosting is additional work.

The hackathon foundation adds internal provider adapters, opt-in Atlas evidence projection/search, explicit Databricks agent-metrics commands and server diagnostics. It does not add customer-facing sponsor installation cards or integrate those services into imported repositories. These do not retroactively change the 1d094be review evidence. Atlas worker retrieval and actual cloud/model acceptance remain assigned work; no automatic cloud upload is implied. See [TEAM_PLAN.md](TEAM_PLAN.md).

## Provenance and public submission

The baseline README says 1d094be consolidates earlier development. Historical artifact commit IDs predate that initial history. A history reset does not establish authorship dates. Disclose the baseline, identify event work and resolve the supplied all-code-during-the-event eligibility rule with organizers. Publish the required repository and accurate README/model/API list with truthful provenance.

Not checked: fresh backend/browser/sandbox execution, credentials, sponsor endpoints, external provisioning, every source path or a full adversarial security review. No additional implementation bug is asserted without a concrete observed case. Main risks are model reliability, integration time, service access and unresolved eligibility.

## Foundation follow-up

The subsequent [foundation verification](../evidence/hackathon-foundation/README.md) closes the aggregate-evidence gap: 541 collected backend passes, real restart, production build and 16 browser passes, plus 112 final post-review contract passes. This does not close live model qualification or prove cloud-service improvement. The review above retains its original baseline scope.
