# Frozen 3B baseline qualification

All 12 tasks were attempted against commit `ad9a3d46f9f5a3cbfb8c75b0e3b7b9c62e83f67d` and production aggregate `0730f8ad474740c65adb78c37f1605f0ca8ac6b371a45f1965d0a61e6f738408`. The last two were externally incomplete; they are not successful quality evaluations.

Passed 2/12. Qualified: false. Genuine failures are retained; schema/health smoke is not counted as qualification.

CPU-only Qwen2.5-Coder-3B Q4_K_M, 4 threads, context 16384, global output limit 1536, 240-second per-attempt timeout. Embeddings explicitly disabled; keyword/graph retrieval enabled. Runtime paths/settings are in runtime.json.

Runtime limitation: the lead observed multiple owned llama processes with the same verified 3B model/endpoint arguments during this baseline. Their PIDs, reported memory and other resident model processes are in runtime.json. Latencies are actual elapsed observations under contention, not an exclusive-process speed benchmark.

| Case | Passed | Seconds | Provider attempts | Known output tokens | Unknown usage attempts | Test corrections |
|---|---:|---:|---:|---:|---:|---:|
| python-bug-regression | True | 348.68 | 5 | 983 | 0 | 0 |
| python-decision-feature | False | 334.02 | 4 | 1554 | 0 | 1 |
| python-revision | False | 239.31 | 4 | 909 | 0 | 1 |
| python-knowledge | False | 317.78 | 4 | 1097 | 0 | 1 |
| python-cancellation-restart | True | 318.81 | 6 | 1041 | 1 | 0 |
| python-unsafe-review | False | 17.30 | 1 | 19 | 0 | 0 |
| typescript-bug-regression | False | 345.94 | 4 | 1249 | 0 | 1 |
| typescript-decision-feature | False | 395.82 | 4 | 1539 | 0 | 1 |
| typescript-revision | False | 391.95 | 4 | 1305 | 0 | 1 |
| typescript-knowledge | False | 373.67 | 4 | 868 | 0 | 1 |
| typescript-cancellation-restart | False | 7748.34 | 4 | 381 | 2 | 0 |
| typescript-unsafe-review | False | 14549.14 | 0 | 0 | 0 | 0 |

Each cases/*.json preserves exact typed request payloads and model outputs, telemetry, actual check logs, candidate/revision diffs, and immutable fixture source. Shared role system messages and schemas are in prompts.json. Seeded review requests were reconstructed exactly from the frozen driver and retained bytes; that provenance is explicit. No extra inference was performed for packaging.

Raw isolated data remains at the paths and SHA256 values recorded per case. It contains fixed qualification fixtures only. No credentials are included. Unknown usage remains unknown; known-token subtotals are not claimed as complete totals.

A test-role correction is separate from the driver implementation repair_rounds field. Failed seeded reviews are model false-safe findings; independent holdouts and verification assessment retained the failure, and no seeded case published a revision.

External interruption: the last cancellation case recorded 7748 seconds elapsed and a regression request cancelled after 7593 seconds, exceeding the nominal 1800-second case / 240-second request limits while the monitoring tool also stalled. The final unsafe case obtained no model attempt because a Docker health command with a nominal 30-second timeout returned after 14549 seconds. Host/tool suspension or stalling is suspected; the exact cause is unconfirmed. These two outcomes require rerun after runtime recovery and are not counted as model capability failures. See runtime.json and exact raw diagnostics.

Windows prerequisite: use the short project data/q3b-final root. Earlier long-worktree-path infrastructure attempts are historical and are not substituted into this frozen baseline.
