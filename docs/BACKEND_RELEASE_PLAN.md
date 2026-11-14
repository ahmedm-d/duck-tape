# Backend demo release plan

Goal: finish the existing backend so completed Adaption implementer/test-writer artifacts can be served and qualified without another workflow rebuild.

Architecture: retain FastAPI, SQLite workflow transactions, immutable local source snapshots and Docker checks. Reuse the B/C catalogue, graph and retrieval interfaces. Atlas graph requirement remains cancelled. Databricks graph processing belongs to B and must retain the published context contract.

## Implementation checklist

- [x] Integrate committed workspace routing with catalogue navigation; preserve teammates' uncommitted work.
- [x] Expand model smoke to regression, acceptance and repair; verify strict edit and test coverage contracts.
- [x] Add a resumable operator demo runner with isolated targets, honest blocked states and an evidence manifest.
- [x] Pin bug and advisory cases, dependency artifacts and exact baseline checks.
- [x] Publish B's Databricks compatibility handoff and remove contradictory current-contract claims from historical training docs.
- [x] Probe available model/cloud/sandbox configuration without exposing credentials; run only configured, authorized integrations.
- [x] Run deterministic acceptance, Docker gates and rendered application journeys; fix observed defects.
- [x] Independent review, targeted recheck, commit and final readiness evidence.

## Decisions and limits

- User approved inline implementation of the reviewed plan; no further routine design approval is needed.
- Integrate committed UI branches in this worktree only. Do not merge unfinished edits or change another checkout.
- The Adaption models are training. No finished model, base identity or serving hardware is assumed.
- Public cloud catalogue curation and private Databricks repository processing are different pipelines. Never upload private source to the public catalogue.
- No weakening of output validation, source isolation, network restrictions or approval requirements to make a demo pass.
- Manual Databricks publication/metrics commands are sufficient; automatic cloud export is outside this change.
- External stages must report blocked when unconfigured; fixture/mock results never count as live evidence.

## Review focus

Missing or incompatible test-writer output; stale graph/snapshot returned after refresh; a catalogue record from a different revision; model cancellation followed by a late revision; demo scripts that overwrite user data or mistake mocked evidence for live success.

Tests follow each changed boundary, then the existing `scripts/acceptance.py` deterministic and sandbox tiers. Runtime evidence stays under ignored `data/`; this document records the final commands/results.

## Execution log

- Integrated committed navigation in merge commit `6fc008b`; no teammate checkout changed. Preserved catalogue routing and repaired restorable Files/Knowledge tabs and Activity access. No additional frontend visual work after user steering.
- Added graph repo/snapshot rejection and omitted stale catalogue matches; 28 focused checks passed, 1 optional Graphify CLI check skipped.
- Model smoke covers all seven roles; 40 smoke/provider tests passed.
- Atlas candidate regression reproduced 1.00 local vs 0.75 augmented recall on four fixed files; local-priority graph expansion now gives 1.00/1.00, with a spare-slot test proving Atlas augmentation. Live Atlas relevance remains unverified.
- Current process configuration: cloud integrations disabled and no served model configured. Linux Docker available.
- Actual browser sample journey passed with real patches, checks, revision, decision and managed application. Evidence under `data/release-browser/`.

- Added the repeatable generic demo runner; 16 focused tests passed after independent review. It proves actual worker Atlas context inclusion, test-writer usage, exact checks, cleanup and separate metrics-export status.
- Fresh preparation fetched OSV and passed both actual Docker baselines with five hash-verified dependency wheels. Manifest: `data/backend-demo-final-prepare/backend-demo.json`. Running with no served model returned blocked without consuming approval.
- Actual Linux Docker tier: 15 passed in 566.13 seconds; artifact directory `data/release-acceptance/20261114T220451Z-b471967b/`.
- Final independent backend review: no actionable findings; 18 focused tests passed. Functional route regression recheck: 1 passed.

- First aggregate attempt recorded 728 passed and 1 optional skip with no test failures, but exceeded the 600-second process deadline at shutdown while overlapping Docker load. This acceptance attempt remains failed; retained at `data/release-acceptance/20261114T221042Z-040087f4/`. Rerun uses the unchanged deadline after Docker completed.

- Full backend rerun: **730 passed, 1 skipped**, 598.05 seconds, unchanged 600-second deadline. Optional Graphify CLI unavailable; one existing Starlette/httpx deprecation warning. Artifacts: `data/release-acceptance-isolated/20261114T222117Z-3a430c91/`.
- Production preview restarted at `http://127.0.0.1:8782`; worker/monitoring health running. Actual backend pages (Dashboard, repository, Codebase, nested Job, Settings) rendered without browser console/page errors.

- Final deterministic acceptance **passed all four gates**: backend (730 passed, 1 optional skip), real process restart recovery, production build and 32 browser tests. The earlier deadline failure remains retained. No test assertions or deadlines were relaxed.
- Source changes passed independent review and targeted rechecks; documentation links and staged whitespace checks passed. Work remains isolated on `feat/repo/backend-demo`; no main merge or remote push.

## Remaining live gates (not backend implementation claims)

| Gate | What remains | Owner |
| --- | --- | --- |
| Trained models | Finish training, record base/artifact identities, serve both adapters and a general model, pass all-role smoke | A/operator |
| Public cloud chain | Run Databricks curation, restore/publish to Atlas with scoped accounts, prove matching revision/hash | C/operator |
| Private Atlas retrieval | Configure embeddings/index; opt in and prove selected worker context with the demo runner | B/operator |
| B's new Databricks graph | Integrate the separate producer behind the existing contract; verify real cloud freshness/cancellation | B |
| Live application | Run both generic cases, inspect checks/diffs, exact-revision approval, export/apply and same-run Databricks metrics | Lead |
| Qualification | Pass the held-out 12-task gate and preserve failed attempts before claiming the trained models qualified | A/Lead |

Use [the operator runbook](BACKEND_DEMO_RUNBOOK.md) for commands and [the setup handoff](BACKEND_DEMO_HANDOFF.md) for account configuration. No automatic merge, paid deployment or credential copying was introduced.
