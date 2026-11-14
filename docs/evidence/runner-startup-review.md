# Local runtime startup repair

The installed and legacy local coding launchers now share an OS file lock beside the canonical executable, keyed by port. Lock acquisition is nonblocking. An exclusive local bind probe inside that lock refuses to launch if the port is occupied or unavailable, including when HTTP health times out during loading or inference. Manifest binary/model hash checks remain before launch.

Only the newly spawned child is owned by this attempt. The launcher waits at most 30 seconds for port binding or child exit. A failed, timed-out, or interrupted attempt terminates that child and waits two seconds, then kills that same child and waits at most two further seconds if needed. Existing listeners are never terminated. Successful binding leaves the new runner running; it is not a claim that model loading or inference is healthy. Failures are actionable and coding/embedding errors are exposed by provider health.

CPU coding runners explicitly use one inference slot (`--parallel 1`) for both installed manifests and the legacy launcher. Configured context remains unchanged. Embedding launch arguments retain their existing behavior. An optional manifest `reasoning` field accepts only `off`, `on`, or `auto`, and reaches the coding command as `--reasoning VALUE`; absent fields preserve prior behavior. No arbitrary argument field was introduced.

## Evidence

- [Initial regression RED](runner-startup-red.xml): occupied-port protection, concurrency, bounded startup, and reasoning behavior exposed before implementation.
- [Exception cleanup RED](runner-startup-cleanup-red.xml): owned children remained running after injected post-spawn OSError and KeyboardInterrupt.
- [Single-slot RED](runner-single-slot-red.xml): five exact coding flag assertions failed before the flag change.
- [Final focused and related gate](runner-startup-regressions.xml): tests cover the launcher, provider contracts, startup settings, and final-completion contracts.
- [Focused test source](../../tests/test_runner_startup.py): a real occupied ephemeral TCP port rejects launch despite failed health; two synchronized Python processes with separate worktree roots and one canonical executable start exactly one authored delayed TCP listener. The test verifies exactly one spawned PID and connects to its real listener. Other tests verify normal timeout, early exit, exception/interruption cleanup, legacy caller error propagation, strict manifest validation, and exact coding-only arguments.

All runtime subprocesses in these tests are fixed authored Python helpers. No model weights were loaded, no inference requests were made, and no existing model process or live endpoint was stopped or probed. Windows OS locking and exclusive bind behavior were exercised here; the POSIX `flock` branch is implemented but was not executed on this Windows host. This repair changes future startup and does not remove previously duplicated runners.
