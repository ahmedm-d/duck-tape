# Researcher model

An instruction-tuned specialist. **Input:** one code function plus its `<graph_context>` block (the
v1 handoff contract). **Output:** a JSON object `{"findings": [...]}`. Each finding has `title`,
`description`, `priority` (`low|medium|high|critical`), `cwe` (`CWE-<n>`), `reference`
(`path`, `start_line`, `end_line`) and `source_key`. Fixed or safe code must produce
`{"findings": []}`. Output is untrusted: `src/training/researcher_intake.py` validates it strictly and
maps each finding to a `POST /api/intake` body (`source=security`, `type=security`), so every finding
still goes through normal human approval.

## Data

Training and eval rows are written by `scripts/prep_13_researcher.py` to
`data/processed/researcher.jsonl` and `data/eval/researcher_eval.jsonl` (both gitignored). Sources,
licences, row counts and exclusions are in `data/researcher_report.md` and `data/dataset_licenses.json`.
Every completion must parse with `parse_findings`; the launcher checks this before anything is sent.

## Key

Add one line to `model-training/.env.training` (gitignored, never committed):

```
ADAPTION_API_KEY_RESEARCHER=<key>
```

If it is absent the launcher falls back to `ADAPTION_API_KEY_3`, which is shared with A's two running
DPO jobs (shared credit pool and a 5-concurrent-run cap). A dedicated key is preferred. The key value is
never printed or written to the manifest; only the variable name is shown.

## Commands

Run from the repository root with the training virtualenv (needs the `adaption` SDK, 0.19.0 verified).

```
python model-training/scripts/launch_researcher.py --dry-run     # default; validates data, prints plan, sends nothing
python model-training/scripts/launch_researcher.py --execute     # raw upload, wait, create AutoScientist run, record manifest
python model-training/scripts/launch_researcher.py --poll        # refresh status, iterations, best_win_rate, download_available
python model-training/scripts/launch_researcher.py --poll --download   # also save the artifact when available
```

Options: `--model` (default `Qwen/Qwen3.6-35B-A3B`), `--target-rows` (default 10000; the run asks Adaption
for `max(0, target - train_rows)` synthetic domain rows), `--idempotency-key` (default
`duck-tape-researcher-v1`), `--custom-eval` (opt-in rubric registration, see below).

`--execute` is idempotent: a recorded run is refreshed, never duplicated; a same-named remote dataset
`duck_tape_researcher` and an existing live run on it are reused. If the recorded run failed it stops and
tells you how to relaunch (remove the manifest entry, pass a new `--idempotency-key`).

The run is recorded under `runs[]` in `data/adaption_runs_manifest.json` (`agent: "researcher"`).
`account_index` is `3` when `ADAPTION_API_KEY_3` was used, or `5` (with `account_label: "researcher"`)
for the dedicated key; it must stay an integer because `poll_adaption_runs.py` calls `int()` on it.
`poll_adaption_runs.py` will refresh the `3` case but skips `5` (no client), so use `--poll` here.

`--download` writes `models/checkpoints/researcher_<run_id>.gz` (gitignored area). Never commit weights.

### Custom eval

`datasets.custom_evals.create` scores a rubric against the original and adapted pairs of an adapted
dataset. A raw upload has no adapted pairs, so the rubric may be a no-op; it is therefore off by default
and non-fatal when enabled. The rubric text (valid JSON, empty list for fixed code, correct CWE and lines,
no invented details) lives in `CUSTOM_EVAL_RUBRIC` in the launcher.

## Evaluating base versus adapted

`scripts/evaluate_models.py` and `backend/providers.py` on this branch qualify the configured providers on
the repository's fixture tasks (investigation, planning, implementation, review). They have no researcher
task, so they do not measure this model. The plan:

1. Run the base model and the adapted model on `data/eval/researcher_eval.jsonl` with the same prompts.
2. Score each completion: valid findings JSON (`parse_findings`), empty list on fixed code, CWE match,
   reference overlap with the labelled lines, and rate of findings absent from the labels.
3. Report base and adapted separately, with failures and unparseable outputs counted, never dropped.
4. Adaption's `best_win_rate` is the platform's own metric; it is not a substitute for step 2.

Nothing in this change implements step 1 to 3; it needs the adapted model to be served first.

## Still needed before continuous runtime use

- Obtain and serve the artifact (`--poll --download`, then host it behind an OpenAI-compatible endpoint).
- Add a researcher task and JSON schema to `backend/providers.py` / `backend/model_schemas.py` (owner A;
  shared-file changes go through the integration captain) and wire a worker that feeds functions with graph
  context and posts `to_intake_payloads(...)` to `/api/intake` for an imported repository.
- The base-versus-adapted evaluation above, with fixture, mocked and live results reported separately.
- Local-only handling: repository code sent to the model must stay within the cloud-projection policy
  (disabled by default), and findings stay untrusted evidence.
