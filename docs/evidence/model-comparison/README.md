# Provider smoke comparison (15 Nov 2026)

Three models were run through Duck Tape's provider smoke test (`scripts/evaluate_models.py --smoke`) on the same Windows laptop (Intel Core Ultra 7 165H, 63 GB RAM, no discrete GPU), one run per model and answer limit, between 04:07 and 04:24 UTC.

The smoke test sends 8 real requests through `backend.providers.complete`: investigation, planning, implementation, repair, a review that should approve a correct patch, a review that should reject a wrong one, a regression test and an acceptance test. Answers are validated against the role schemas and checked statically (exact edit paths and hashes, applied file contents, parsed test code, review verdicts, cited line ranges). **Generated code is not executed**; that is the Docker qualification gate, and Docker was not available on this machine. The structured controller was used for every request (`STEWARD_SPECIALIST_MODE=off`).

All runs share the same application source fingerprint (`aggregate_sha256` `921b8a3fd5fb…`, the code committed at `14d12e4`). The `dirty` flag in each file comes from untracked local files only.

## Results

| Model | Provider | Answer limit | Passed | Wall time (8 requests) | Input / output tokens | Cost |
|---|---|---|---|---|---|---|
| `claude-opus-5-5` | `anthropic` | 8192 (Claude reserves 16000) | **8/8** | **37 s** | 20,718 / 1,447 | ~$0.11 at list price ($4 / $20 per 1M) |
| `qwen3.5-397b` | `adaption` | 8192 | **8/8** (one retry after HTTP 504) | 168 s | 6,604 / 9,033 | Adaption credits |
| `qwen3.5-397b` | `adaption` | 4096 | 7/8 (regression `truncated`) | 149 s | 6,604 / 12,806 | Adaption credits |
| `qwen3.5-397b` | `adaption` | 1024 (smoke default) | 5/8 (planning, regression, acceptance `truncated`) | 83 s | 6,604 / 6,237 | Adaption credits |
| `qwen3-coder:30b` | `ollama` (local CPU, 16k context) | 1024 (smoke default) | **8/8** | 199 s | 6,588 / 1,259 | none (local) |

Per-request results, latencies, attempts and outputs are in the JSON files: `claude-8192.json`, `adaption-8192.json`, `adaption-4096.json`, `adaption-1024.json`, `local-1024.json`.

## Observations

- Every `qwen3.5-397b` failure was `truncated`: the model reasons at length before answering (about 9,000 output tokens for answers the other models gave in about 1,300–1,500), so a 1024 or 4096 answer limit cuts it off. Use `STEWARD_MAX_OUTPUT_TOKENS=8192` or more with it, and raise `STEWARD_CONTEXT_LIMIT` to match (prompt plus reserved answer must fit).
- Its one remaining failure at 8192 was an Adaption gateway timeout (HTTP 504 after 60 s); Duck Tape's retry succeeded.
- Claude Opus 5.5 was about 4–5 times faster than either alternative. Its higher input token count reflects a different tokenizer and the schema enforced on Claude's side.
- The local model was accurate on these small tasks but slow on CPU (6–45 s per request).

## Limits of this evidence

One run per configuration, 8 small requests on a single toy repository, static checks only. It shows the providers work end to end through Duck Tape's request and validation path; it is not a reliability benchmark and does not qualify any model for autonomous use.

## Reproduce

```bash
# Claude needs ANTHROPIC_API_KEY in .env; Adaption uses ADAPTION_API_KEY (or ADAPTION_API_KEY_1 from model-training/.env.training);
# local needs Ollama serving qwen3-coder:30b with OLLAMA_CONTEXT_LENGTH of at least 16384.
python scripts/compare_models.py local adaption
python scripts/compare_models.py adaption --max-output 4096
python scripts/compare_models.py claude adaption --max-output 8192
```

Results go to `data/model-comparison/` (gitignored), with each model in its own scratch store.
