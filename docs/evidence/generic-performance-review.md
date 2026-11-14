# Generic performance verification

Supported profile discovery now includes an existing Python `benchmark.py` (preferred) or `benchmarks/benchmark.py`. For npm projects with the existing required lockfile, `scripts.benchmark` accepts exactly `node <relative.js>` or `node --experimental-strip-types <relative.ts>` when that file exists. Unsupported paths, flags, shell operators, missing files, and other scripts remain unfamiliar commands; no shell execution or network permission was added.

Discovered commands use `id: benchmark`, `purpose/kind: benchmark`, `required: true`, the existing 300-second command cap, and `benchmark_harness: [path]`. The plan fingerprint includes this metadata. Verification binds baseline harness bytes to SHA-256 hashes and rejects candidate modification or deletion with a required failed `benchmark_integrity` check. Benchmarks run before other repository commands and only in baseline/candidate phases, never in the test-only regression phase. Missing before/after evidence, changed harness hashes, different image/dependency environments, different base/patch bindings, unequal or empty workloads, fewer than three raw samples, and nonpositive/nonfinite samples cannot establish a valid comparison. Existing test-integrity and advisory checks still apply. The worker owns the performance-job requirement for a valid measured positive improvement.

Tests were observed RED before implementation (7 failures), then all scoped adapter/discovery/verification tests passed. The final combined run, including the qualification driver's approval-state compatibility update, passed **41 tests in 0.49 seconds**. Discovery tests cover both Python paths and exact JavaScript/TypeScript argv; rejection tests cover traversal, absolute paths, missing scripts, shell composition, incompatible flags and extensions. Verification regressions cover edited/deleted harnesses, missing after-run, changed hashes, unchanged harness bindings, and avoiding benchmark execution during regression-before tests.

An actual run used the existing pinned Python Docker runtime, offline dependencies, network disabled, nonroot user, read-only root and bounded scratch. The unchanged harness measured summation of 10,000 values repeated 200 times per sample, checked the expected total, and recorded five `perf_counter` samples for both the original Python loop and a candidate using built-in `sum`. No model was called. Raw evidence, exact image/dependency identity, harness hash, check outputs and the tampering rejection are preserved in [generic-performance-run.json](generic-performance-run.json).

| Measurement | Baseline seconds | Candidate seconds |
| --- | ---: | ---: |
| Sample 1 | 0.030720184 | 0.007159980 |
| Sample 2 | 0.028631654 | 0.007324272 |
| Sample 3 | 0.028518350 | 0.006977954 |
| Sample 4 | 0.041779408 | 0.008494964 |
| Sample 5 | 0.029505573 | 0.006672768 |
| Median | 0.029505573 | 0.007159980 |

The observed median improvement was **75.7335%**. This is a measured result for this fixture and run, not a claim about model-generated optimizations or general application speed. A second actual Docker verification changed the harness to print a fake win: ordinary baseline/candidate tests passed, but required harness integrity failed, no benchmark comparison was accepted, and overall verification failed. No fabricated improvement or skipped runtime check is presented as success. This performance fixture adds no regression test, so assessment explicitly reports that limitation.

Limitations: only the discovered entrypoint is frozen; helper modules implementing the application may change because those are the optimization target. Projects requiring custom benchmark runners, alternative argument forms, or a missing harness remain unsupported. Timing variability and workload representativeness remain visible through raw samples and the declared workload. The full real model qualification corpus runs separately under the model agent.

Qualification compatibility: the driver now handles a persisted `Awaiting approval` plan after investigation. It records explicit approval of the actual hash, steps and acceptance, uses the job's expected record version, distinguishes initial investigation consent, and refuses a hash without actual steps. Cancellation/retry follows the same approval flow. These changes do not bypass the application's plan approval boundary or call a fixture provider.
