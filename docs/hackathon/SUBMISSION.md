# Submission facts and checklist

Working project: **Duck Tape**. Proposed track: **Autonomous Agents**. Additional prize target: **Adaption**, subject to current organizer/sponsor requirements and actual qualifying work.

**Supplied deadline: Sunday 15 November 2026 at 12:00 Europe/London (12:00 UTC).** The team target is 11:00. This rule summary comes from supplied event information; confirm current announcements and the submission portal. Do not invent a confirmation.

## Internal architecture to describe

Hugging Face and Adaption provide **Duck Tape's own model inference/adaptation**; MongoDB Atlas provides its **internal evidence memory/retrieval**; Databricks provides its **internal agent-run evaluation and improvement measurements**. Imported/client repositories supply evidence and receive reviewed maintenance patches. Duck Tape does not install sponsor services, SDKs, credentials or setup flows into those repositories.

The [revised team plan](TEAM_PLAN.md) also proposes a public global catalogue served by Atlas, with Databricks ingestion/normalization/deduplication/freshness and publication. B matches those public findings to private repository evidence locally. These are planned additions, not existing foundation capabilities; describe them only if the submitted build supplies evidence.

State actual implementation maturity: the foundation has adapters and explicit operator commands, not a configured/live-qualified stack or an automatic improvement loop. Atlas worker wiring, any automatic internal processing, actual cloud acceptance and any model adaptation must be described according to evidence from the submitted build.

## Facts to complete

Replace each TBD with a checked fact or explicitly mark it unavailable. These are working fields, not claims of completed submission.

| Field | Actual value |
| --- | --- |
| Team name and three contributors | TBD |
| Public GitHub repository URL | TBD |
| Submission commit SHA | TBD |
| Submission portal / entry URL | TBD |
| Confirmation identifier and submission time | TBD |
| Demo recording URL and duration | TBD |
| Reproduction instructions for the demonstrated run | TBD |
| Demo machine, runtime and sandbox | TBD |
| Duck Tape internal base inference provider/model/version | TBD |
| Internal Adaption usage and adapted agent model/version, if any | TBD; an API adapter alone is not an adaptation result |
| Hugging Face model/provider/revision, if used | TBD |
| Embedding provider/model and vector dimensions | TBD |
| Internal Atlas evidence-memory index, namespace filters and worker retrieval evidence | TBD; omit credentials/connection secrets |
| Internal Databricks agent-evaluation table/query and exported comparisons | TBD; redact secrets/private account details |
| Global catalogue sources, Databricks processing/publication and Atlas queries | Planned; replace with actual run/revision evidence if implemented |
| Other models, APIs, datasets and open-source dependencies | TBD; include licenses/attribution where required |
| Evaluation tasks, holdout separation and actual results | TBD |
| Known failures, unmeasured claims and unavailable integrations | TBD |
| Existing-code provenance and organizer eligibility response | TBD; no unsupported attestation |

## Honest project description

Use only after reconciling it with the submitted build:

> Duck Tape is a local AI-assisted maintenance workspace. It retrieves repository evidence and approved decisions, prepares isolated patches, executes supported checks and asks a developer to review the exact revision. This submission demonstrates [actual live scenario] using [exact internal model/API]. Duck Tape uses [actual internal Atlas evidence memory, Databricks run evaluation and Adaption/Hugging Face inference/adaptation, with measured results]. Imported repositories supply evidence and receive reviewed maintenance patches. [Material limitation or unavailable integration.]

Do not describe fixture inference as a live model, configuration as connectivity, an adapter as model adaptation, a completed stage as verified correctness, or a small evaluation as universal autonomous coding reliability. Report actual results, including no improvement.

## Existing code and eligibility

Initial commit **1d094be** contains a substantial existing application and historical evaluation artifacts. The README explains that it consolidates earlier development history. Resetting history does not change when code was written.

The supplied rules require code to be written during the hackathon while permitting pretrained models, APIs and allowed open-source work. That exception does not itself establish eligibility for this team's existing application. Disclose the baseline and event work, retain the organizer's actual response if sought, and follow their determination. Do not claim eligibility, sponsor qualification or all-event authorship without support.

| Provenance field | Actual record |
| --- | --- |
| Existing baseline | Initial commit 1d094be; earlier build/evaluation notes retained |
| Event start time from official rules | TBD |
| Work completed during the event | TBD; link actual commits/diffs and artifacts |
| Organizer clarification, if needed | TBD; date/source and actual response, not invented approval |
| Final wording used in submission | TBD |

## Final checklist

- [ ] Confirm deadline, track, prize requirements and required submission fields with current organizer announcements.
- [ ] Resolve or clearly disclose the existing-baseline eligibility issue; do not make a false provenance statement.
- [ ] Make the GitHub repository public and verify its URL without a privileged login.
- [ ] Ensure README has setup/demo instructions and an accurate model/API/dependency list.
- [ ] Pin the submission commit and list work actually done during the event.
- [ ] Review changed tracked files/evidence for credentials, connection strings, personal access codes and private data.
- [ ] Describe sponsor services as Duck Tape's internal infrastructure; confirm imported/client repositories receive no sponsor SDKs, credentials or setup changes.
- [ ] Match cloud-data disclosures to implementation and keep uploads opt-in.
- [ ] Run current deterministic acceptance and relevant focused tests; attach actual results and retain failures/blockers.
- [ ] Run the live demonstration on the submitted build; retain before/after checks and the unsafe-review outcome.
- [ ] Record actual Atlas worker retrieval and Databricks query results if claiming those integrations.
- [ ] Separate training from held-out evaluation and retain base/adapted outcomes if claiming adaptation.
- [ ] Label fixtures, cached/pre-recorded results and incomplete external runs.
- [ ] Check recording, evidence links and reproduction instructions from a fresh reader's perspective.
- [ ] Submit early, check submitted fields/links and retain real confirmation before noon.

## Evidence register

Use relative paths for repository artifacts and public URLs only where needed. Include exact commands/dates. Do not overwrite historical evidence to make it look current.

| Claim | Command / scenario | Current artifact | Outcome and limit |
| --- | --- | --- | --- |
| Deterministic application checks | python scripts/acceptance.py --tier deterministic, in the installed environment | TBD | TBD |
| Generic live repair | Exact provider/model and request: TBD | TBD | TBD |
| Regression fails before / passes after | Exact check commands: TBD | TBD | TBD |
| Unsafe proposal handling | Seeded case: TBD | TBD | TBD |
| Approved knowledge affects a later request | Scenario and approved version: TBD | TBD | TBD |
| Duck Tape uses its internal Atlas memory | 2026-11-14, Atlas M0, Ollama `nomic-embed-text` (768 d): `data_platforms.atlas_sync` then `atlas_search` on the Parcel sample; `retrieve(..., atlas=True)` comparison | [RETRIEVAL.md](../RETRIEVAL.md#atlas-candidates-opt-in) | Live: 14 chunks uploaded, 0 excluded, index queryable; search returned local-validated chunks (scores 0.85–0.88). Recall versus local fallback mixed on 3 queries; no gain claimed. Worker run with Atlas enabled still TBD. |
| Databricks evaluates Duck Tape agent runs | 2026-11-14, Free Edition serverless warehouse: `python scripts/integrations.py databricks-sync --run <sample run>` then `databricks-summary` | Command output; table `workspace.default.ducktape_runs` | Live: 1 row merged and queried back (complete, 1637 ms, tokens unknown/NULL). Run used the offline sample fixture provider, so `verified=false` by design; a live-model run is still needed. |
| Global source becomes a repository-specific proposal | Public source -> catalogue revision -> local snapshot match: TBD | Planned | Not implemented by the planning docs |
| Internal agent base/adapted comparison, if performed | Held-out manifest and settings: TBD | TBD | TBD |
| UI/build and demo | npm run build; exact browser/demo checks: TBD | TBD | TBD |

The foundation does not come with live credentials or sponsor-account acceptance evidence. Missing or failed services remain limitations until an actual run establishes otherwise.
