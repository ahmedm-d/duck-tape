# Hosted base-model demo

This run uses Adaption-hosted base models, **not the exported AutoScientist LoRA specialists**. Specialist mode is disabled. Downloaded checkpoints remain unchanged.

## Configuration

The local ignored `.env` holds the API credential. Never copy it into frontend configuration or commit it.

```dotenv
STEWARD_MODEL_PROVIDER=openai
STEWARD_MODEL_ENDPOINT=https://api.prod.adaptionlabs.ai/api/v1
STEWARD_MODEL=qwen3.5-397b
STEWARD_SMALL_MODEL=qwen3.5-397b
STEWARD_SPECIALIST_MODE=0
STEWARD_CONTEXT_LIMIT=128000
STEWARD_MAX_OUTPUT_TOKENS=8192
STEWARD_MODEL_TIMEOUT=90
# STEWARD_MODEL_KEY is set privately on the server.
```

Adaption's chat compatibility endpoint uses JSON-object output plus Duck Tape's strict schema validation. A live comparison found lost code newlines with its JSON-schema mode. Other OpenAI-compatible endpoints keep JSON-schema mode. The worker allows the configured response budget up to 8,192 tokens; the 12-call / 30,000-output-token job budget stays enforced.

## Repeatable qualification

With the environment loaded, run:

```powershell
python scripts/evaluate_models.py --driver scripts.qualification_driver:driver --output data/base-model-e2e-final/results.json
python scripts/acceptance.py --tier deterministic
```

Qualification uses fresh stores and source copies, live model requests, isolated Docker checks, and independent behavioral holdouts. It covers Python and TypeScript bug reproduction, decisions, revisions, knowledge privacy/invalidation, cancellation/restart and unsafe-review rejection. Fixture browser tests are separate evidence, not live-model qualification. Nothing pushes to the demo GitHub repository.

## Manual demo

1. Open Settings and check the actual model ID and Docker availability.
2. Connect the public demo repository; inspect Files, graph and source evidence.
3. Inspect the Atlas global catalogue and local vulnerability applicability. A version match does not prove exploitability.
4. Create a bug ticket describing quantity being ignored in quote totals. Approve investigation and the resulting plan when requested.
5. Wait for actual regression-before and candidate checks, then inspect the patch and model/check evidence.
6. Add a Team note; request an AI revision separately and compare preserved patch versions.
7. Export the patch. Approval and managed local application are separate actions; neither is a remote GitHub PR.

The prepared additions/deletions in `C:/Users/ahmed/Projects/duck-tape-demo-kit` are still user-controlled and have not been pushed by this run.

## Results

Live Qwen result: one bug-fix journey passed through real fail-before/pass-after Docker checks, independent behavior checks, model review, patch export validation, changed-files ZIP, revision approval and managed application (`Completed`).

The full live suite is **not qualified**: decision output failed schema validation; revision acceptance output used an invalid Python test reference; knowledge and cancellation/restart cases encountered HTTP 429. Remaining live cases were stopped after consecutive rate-limit failures. These are not passes. See `data/base-model-e2e-final/results.json`, per-task `qualification-result.json`, and `apply-export.json`.

Deterministic evidence: 36 provider tests and 27 worker/pipeline tests passed; production frontend build passed; 20 browser fixture checks passed. Abrupt worker kill and API restart recovered one checked fixture revision and approved knowledge (port 8794; the script's default 8766 was occupied). The full backend suite passed: **861 passed, 1 skipped** in 752.60 seconds; evidence is in `data/base-model-e2e-final/backend.log` and `backend.xml`. The initial all-gates wrapper exceeded its 600-second backend deadline; separate gate results must not be reported as a passing wrapper run.

Live Atlas probes: repository context `scope.atlas.served=true`; global requests advisory graph has 3 nodes and 2 edges. This does not establish new Databricks ingestion or model deployment.

The provider cache version was advanced after review to prevent reuse of results from the former output format. No tests or execution gates were weakened.

## Running workspaces

- Main workspace: http://127.0.0.1:8783 - existing demo imports preserved, hosted Qwen base model configured.
- Completed live test: http://127.0.0.1:8795/local/qualification-python-bug-regression/jobs/85ac1348-e6b9-4078-999c-fb4ff23f6237 - isolated qualification store, Atlas projection disabled.

Restart the main workspace from this checkout in PowerShell:

```powershell
$env:STEWARD_DATA_DIR='data/atlas-required-live/20261114T231930Z/store'
& 'C:/Users/ahmed/Projects/ducktape/steward/.venv/Scripts/python.exe' run.py --port 8783
```

Do not start a second server on an occupied port. Model requests may continue to fail until Adaption's rate limit clears. AutoScientist specialists remain undeployed; no remote PR was created or GitHub demo change pushed.
