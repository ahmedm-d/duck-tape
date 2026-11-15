# Duck Tape provider smoke comparison

Recorded 15 November 2026. **Claude and Adaption-hosted Qwen each passed all eight static smoke cases.** This verifies model requests, structured output validation, exact edits and review decisions on a toy repository. It does not execute generated tests and is not full Docker/application qualification. These are base models, not the team's trained Adaption checkpoints.

| Model | Current result | Wall time | Attempts | Total input / output tokens | Estimated API cost |
| --- | --- | --- | --- | --- | --- |
| Claude `claude-opus-5-5` | 8/8 passed | 36.2 s | 8 | 20,718 / 1,466 | $0.1122 |
| Adaption-hosted `qwen3.5-397b` | 8/8 passed | 242.1 s | 9 | Unknown: one failed attempt supplied no usage | Unknown |
| Local Ollama `qwen3-coder:30b` | Blocked: all 8 requests failed transport | 45.0 s | 16 | Unknown | Not estimated |

The local result means no reachable runner on this machine, not that the model answered incorrectly. An initial Claude attempt also lacked credentials and returned provider errors; after the key was configured, the separate successful run above was recorded. Original failed-run evidence remains in ignored `data/benchmark-20261115/`.

Adaption's regression-generation request received HTTP 504 once, then succeeded. Its 113.4-second case time includes that retry. Other Adaption cases took 5.3-54.2 seconds; Claude cases took 1.6-4.0 seconds. These are one-run observations, not general performance guarantees.

## Evidence

- [Combined summary](docs/evidence/benchmark-20261115/summary-8192.json)
- [Claude requests/results](docs/evidence/benchmark-20261115/claude-8192.json)
- [Adaption requests/results](docs/evidence/benchmark-20261115/adaption-8192.json)
- [Local transport failures](docs/evidence/benchmark-20261115/local-8192.json)
- [Identity comparison and supplementary hashes](docs/evidence/benchmark-20261115/identity-notes.json)

All eight cases use the same fixture: investigation, planning, implementation, repair, approve-correct review, reject-incorrect review, regression test generation and acceptance test generation. Passes require the expected schemas and static content checks, not merely HTTP success. No generated code was executed by this benchmark.

## Protocol and limits

- Caller output allowance: **8,192 tokens per attempt**, including Claude thinking. Anthropic's optional cap cannot expand this allowance.
- Controller context-accounting ceiling: **128,000**. This is not a claim about Ollama's actual loaded context size; the local runtime controls that separately.
- Per-attempt provider timeout: **120 seconds**; maximum **two attempts per case**, with the existing 0.5-second retry backoff. Whole model subprocess deadline: 3,600 seconds.
- Specialist routing disabled; main and small-model targets both explicitly set to the selected model. Inherited task-specific model overrides are removed.
- Claude: official Anthropic SDK Messages API, schema-constrained output, default effort `high`.
- Adaption: OpenAI-compatible chat endpoint, JSON-object output and strict local schema validation.
- Ollama: local chat endpoint and schema-constrained output; no new model download was attempted.
- Usage sums every attempt when supplied. Missing usage remains unknown, including missing usage on retries. Claude's estimate uses $4/million input and $20/million output tokens; it is not a billing statement. [Official model pricing](https://platform.claude.com/docs/en/models/opus-5-5/overview)

The integration branch changed while these runs were executing: both successful reports have `source_identity_stable: false`. Their recorded identities differ in `backend/main.py` and `run.py`; the recorded provider, adapter-prompt, model-schema and smoke-script hashes match. The Anthropic adapter and comparison script were not in the existing fingerprint allowlist; supplementary hashes were captured after the runs and are labelled accordingly. These results therefore do not qualify the exact final merged commit. Repeat on a frozen commit for release evidence.

Earlier [historical comparison](docs/evidence/model-comparison/README.md) used different budgets and protocols: Claude could reserve 16,000 tokens despite an 8,192 caller limit, Adaption used the Responses API, and local ran with a 1,024 output limit. Keep that evidence, but do not combine its numbers with this table to claim a fair three-model ranking. The current local leg must be rerun by B.

## Instructions for B: run the local leg

Use commit **`a47cff928b67308edc4ab11309b28ad7a1f36503`** in your own clean checkout/worktree; do not reset or switch a teammate's working copy. It contains the reviewed comparison script. Run from the repository root with the project Python environment and existing locked dependencies installed.

```powershell
git rev-parse HEAD
ollama list
ollama ps
python scripts/compare_models.py local --max-output 8192 --output data/benchmark-b-local
```

The expected existing model is **`qwen3-coder:30b`**, served at **`http://127.0.0.1:11434`**. Start your existing Ollama service if necessary; do not substitute another model under this label. The script fixes the 8,192 output allowance, 128,000 controller ceiling, 120-second attempt deadline and two-attempt policy above. It does not change Ollama's loaded context or hardware configuration.

Return these files, plus CPU/GPU/RAM, Ollama version, actual loaded context, and whether the model was warm or cold:

```text
data/benchmark-b-local/local-8192.json
data/benchmark-b-local/summary-8192.json
```

Preserve failures. A nonzero exit means at least one comparison failed or could not run; do not relabel transport, timeout or truncation failures as successful answers. Never send `.env` or API keys with the report.

For a later frozen three-provider rerun with credentials available server-side:

```powershell
python scripts/compare_models.py claude adaption local --max-output 8192 --output data/benchmark-frozen
```

After provider smoke passes, run the Docker-backed application qualification and an actual ticket-to-reviewed-patch journey. This report does not establish trained-checkpoint performance, repository-wide retrieval quality, GitHub publication or successful application of a model-generated patch.
