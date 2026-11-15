# Backend demo operator runbook

The backend runs before trained models are available. The generic model demo becomes executable only after a served endpoint passes schema smoke and Docker qualification. Downloading an Adaption checkpoint alone does not create that endpoint.

## Prepare now

Use Python from the project environment. Build the reviewed Linux Docker images with `python scripts/setup.py --sandbox` if absent. From the repository root:

```sh
python scripts/demo_backend.py prepare --workspace data/my-backend-demo --refresh-public --verify-baseline --allow-dependency-downloads
python scripts/demo_backend.py inspect --workspace data/my-backend-demo
```

Choose a new directory: preparation refuses to overwrite an existing workspace. This fetches actual public OSV evidence, creates two isolated authored repositories, indexes their immutable snapshots, records retrieval citations, creates approval-gated jobs, and runs unchanged baselines in Docker. Dependency downloads are hash-verified artifacts installed only inside the controlled sandbox. The bug target has no third-party dependencies. The security target pins requests 2.31.0 and four transitive packages; its README includes a fixed candidate hash. It demonstrates an affected version, not exploitability or an all-advisories clean bill.

The overall manifest remains `blocked` until actual model work completes. Exit code 2 means blocked, 1 means failed, 0 means the requested demo evidence passed. Preparation with passing baselines may therefore exit 2: inspect the recorded stage results, not just the shell exit code. Omitting `--refresh-public` uses a clearly attributed captured advisory, never a claimed live fetch.

The fixture **sources** are authored for this demo. They are registered as ordinary imported repositories (`is_sample=false`), so the worker uses generic inference. There is no fixture provider branch for these scenarios.

## When training finishes

1. Record original/augmented run IDs, exact base-model IDs, artifact SHA256 and evaluation results. Download successful checkpoints outside Git.
2. Serve the matching base plus adapters at one compatible endpoint, or obtain actual hosted serving IDs. All roles currently share one endpoint. A training run ID is not automatically a serving ID.
3. Configure the server-side `.env`: general model for investigation/planning/review; implementer for `STEWARD_IMPLEMENTATION_MODEL` and `STEWARD_REPAIR_MODEL`; test writer for `STEWARD_REGRESSION_MODEL` and `STEWARD_ACCEPTANCE_MODEL`. See [provider setup](BACKEND_DEMO_HANDOFF.md#when-adaption-finishes).
4. Run all-role smoke, then the selected demo. Do not loosen the structured edit schema to accept arbitrary shell commands or Markdown diffs.

```sh
python scripts/evaluate_models.py --smoke --output data/model-smoke.json
python scripts/demo_backend.py run --workspace data/my-backend-demo --case bug
```

The first run grants investigation consent and stops when a concrete plan needs approval. Read its steps, acceptance criteria and returned `plan_hash`, then resume that exact plan:

```sh
python scripts/demo_backend.py run --workspace data/my-backend-demo --case bug --approve-plan RETURNED_HASH
python scripts/demo_backend.py run --workspace data/my-backend-demo --case security --allow-dependency-downloads
python scripts/demo_backend.py run --workspace data/my-backend-demo --case security --allow-dependency-downloads --approve-plan RETURNED_HASH
```

Decisions, failures and cancellations stop the runner for explicit resolution. It does not choose options, retry failures silently, approve patches or merge code. Passing bug evidence requires actual test-writer, implementer and review calls, a failing-before regression, passing candidate checks and an independent Docker behavior holdout. The security case requires the actual advisory recheck. Both export the exact checked patch and retain IDs/hashes in `backend-demo.json` and per-run records.

To inspect the same jobs in the application, stop the runner first, set `STEWARD_DATA_DIR` to the absolute `<workspace>/store` path and `STEWARD_GLOBAL_CATALOGUE_PATH` to `<workspace>/catalogue.sqlite3`, then launch `python run.py --port 8782` on a free port. Use job APIs/UI for decision selection, retry and final exact-revision approval/application. Do not run a second worker against that store while the runner owns it.

## Real sponsor chain

Use the existing [Atlas and Databricks setup](BACKEND_DEMO_HANDOFF.md). Public curation, private vectors, B's private Databricks graph and metrics export are separate steps.

- Run actual Databricks curation, retain its cloud run ID, restore Delta output and publish it to the public Atlas catalogue using the scoped publisher. Configure the app's separate read-only user.
- Prepare a fresh demo with `--catalogue atlas` instead of `--refresh-public`. Verify its selected entry revision/hash against the cloud curation record.
- Add `--atlas-retrieval` only after configuring private vector credentials, embedding model/dimensions and index. This explicitly permits source-derived embedding/vector upload for the demo fixtures. The runner requires Atlas evidence in the actual worker context, not just in a separate query.

The isolated demo/qualification controllers suppress background graph autosync even when the operator's normal app requires Atlas. `--atlas-retrieval` performs its explicit synchronous vector upload. This prevents daemon threads outliving a temporary store; separately run the app's required-Atlas startup/projection/graph check for full integration evidence.
- Add `--export-metrics` to export the exact verified terminal run through the existing retry-safe Databricks MERGE. Export failures stay visible and do not erase successful patch verification.
- Keep the Databricks curation run evidence with the generated manifest. The runner cannot establish scheduled cloud delivery from a local record or configuration flag.

## Final release gate

```sh
python scripts/evaluate_models.py --driver scripts.qualification_driver:driver --output data/model-qualification.json
python scripts/acceptance.py --tier deterministic
python scripts/acceptance.py --tier sandbox
```

Require `qualified: true` for the full 12-task model gate. Original and augmented models must use the same cases if comparing them. Keep failures and unknown usage. The two-case runner does not replace full qualification.

B's new Databricks graph integration must also pass the [consumer contract](REPO_GRAPH_HANDOFF.md) and fresh cloud checks before being described as connected. Frontend visual work remains on the teammate's branch.
