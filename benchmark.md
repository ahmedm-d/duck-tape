# Duck Tape provider smoke comparison

Recorded 15 November 2026. **Claude and Adaption-hosted Qwen each passed all eight static smoke cases; B reports the local Qwen leg also passed 8/8.** The local result below is teammate-reported: its original JSON files have not yet been received or independently checked here. This verifies model requests, structured output validation, exact edits and review decisions on a toy repository. It does not execute generated tests and is not full Docker/application qualification. These are base models, not the team's trained Adaption checkpoints.

| Model | Current result | Wall time | Attempts | Total input / output tokens | Estimated API cost |
| --- | --- | --- | --- | --- | --- |
| Claude `claude-opus-5-5` | 8/8 passed | 36.2 s | 8 | 20,718 / 1,466 | $0.1122 |
| Adaption-hosted `qwen3.5-397b` | 8/8 passed | 242.1 s | 9 | Unknown: one failed attempt supplied no usage | Unknown |
| Local Ollama `qwen3-coder:30b` (B, CPU, warm) | 8/8 reported; raw evidence pending | 184.6 s | 8 | 6,588 / 1,248 reported | Not estimated |

The original local attempt on the integration machine failed transport because no runner was reachable. That evidence remains unchanged; B subsequently ran the same command on a different machine and reported the result above. An initial Claude attempt also lacked credentials and returned provider errors; after the key was configured, the separate successful run above was recorded. Original failed-run evidence remains in ignored `data/benchmark-20261115/`.

Adaption's regression-generation request received HTTP 504 once, then succeeded. Its 113.4-second case time includes that retry. Other Adaption cases took 5.3-54.2 seconds; Claude cases took 1.6-4.0 seconds. These are one-run observations, not general performance guarantees.

## Evidence

- [Integration-machine summary (retains the unavailable local attempt)](docs/evidence/benchmark-20261115/summary-8192.json)
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

The integration branch changed while these runs were executing: the two hosted-provider reports have `source_identity_stable: false`. Their recorded identities differ in `backend/main.py` and `run.py`; the recorded provider, adapter-prompt, model-schema and smoke-script hashes match. The Anthropic adapter and comparison script were not in the existing fingerprint allowlist; supplementary hashes were captured after the runs and are labelled accordingly. These results therefore do not qualify the exact final merged commit. Repeat on a frozen commit for release evidence.

Earlier [historical comparison](docs/evidence/model-comparison/README.md) used different budgets and protocols: Claude could reserve 16,000 tokens despite an 8,192 caller limit, Adaption used the Responses API, and local ran with a 1,024 output limit. Keep that evidence, but do not combine its numbers with this table to claim a fair three-model ranking. B has now reported the current local leg; ingest and verify its raw reports before treating the three-way table as independently verified.

## Local result reported by B

Run: 15 November 2026, 05:14:44-05:17:49 UTC, exit 0, clean worktree at `a47cff928b67308edc4ab11309b28ad7a1f36503`. B reports `dirty: false`, `source_identity_stable: true`, no reruns, no retries and no edited results. Python 3.15; source fingerprint `5b62389532712b7b2adacec4190bef292aaeffd0216e2da750a3a6c267a1770d`.

```powershell
python scripts/compare_models.py local --max-output 8192 --output data/model-comparison-local
```

| Case | Reported result | Latency | Input / output tokens |
| --- | --- | --- | --- |
| Investigation | PASS | 9.9 s | 266 / 80 |
| Planning | PASS | 14.7 s | 504 / 101 |
| Implementation | PASS | 40.7 s | 1,387 / 215 |
| Repair | PASS | 17.6 s | 1,412 / 179 |
| Review (approve) | PASS | 6.0 s | 331 / 15 |
| Review (reject) | PASS | 5.8 s | 323 / 76 |
| Regression | PASS | 41.3 s | 1,213 / 267 |
| Acceptance | PASS | 43.3 s | 1,152 / 315 |

Runtime and hardware, as reported by B:

- Ollama 0.40.16, `qwen3-coder:30b`, GGUF Q4_K_M, 30.5B parameters, CPU only. Model digest: `06c1097efce0431c2045fe7b2e5108366e43bee1b4603a7aded8f21689e90bca`.
- Intel Core Ultra 7 165H, 63.4 GB RAM, Windows 11 Pro build 26300. Integrated GPU unused; model file 17.28 GiB, loaded allocation 30.12 GiB, about 5.7 GiB RAM free.
- Ollama allocated 131,072 context tokens (12,288 MiB f16 KV cache); controller ceiling 128,000. Prompts reached only about 1,400 tokens, so long-context behavior was not tested.
- Model was already warm after a separate 27-second load/probe. No reloads, truncation or timeouts were reported. The 184.6-second benchmark excludes this preload; a cold run would incur loading time.

These timings compare different models, transports and hardware. They do not isolate model quality or establish a general speed ranking. Provider tokenizers and accounting also differ. Local electricity/hardware costs are not measured.

### Raw evidence handoff still required

The files remain on B's machine in `C:/git/dt-demo/data/model-comparison-local/`; that path is unavailable on the integration machine. The following hashes and sizes are **reported by B**, not independently verified yet:

| Original file | Bytes | SHA-256 |
| --- | ---: | --- |
| `local-8192.json` | 22,461 | `9a659b8df8ef3c8c32768cd87bcd6de1c6fb79b568df6434edc04f5f35ce88e8` |
| `summary-8192.json` | 1,838 | `43827f9ca6ac476a3ae3c5ec921cce71c6dd8b2d447ef8013bf1ec8370720fd3` |

B should copy the original bytes into `docs/evidence/benchmark-20261115/b-local/` on a branch and share the commit. Preserve the existing integration-machine failure reports. Preserve byte hashes when adding files (for example, path-specific `-text` attributes if Git would normalize line endings). Keep the worktree until the reports are received and their hashes, source identity, per-case outputs and totals have been checked. Never include environment files or keys.

For a later frozen three-provider rerun with credentials available server-side:

```powershell
python scripts/compare_models.py claude adaption local --max-output 8192 --output data/benchmark-frozen
```

After provider smoke passes, run the Docker-backed application qualification and an actual ticket-to-reviewed-patch journey. This report does not establish trained-checkpoint performance, repository-wide retrieval quality, GitHub publication or successful application of a model-generated patch.
