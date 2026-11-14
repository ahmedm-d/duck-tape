# Installed API shutdown/reuse fixture check

Ran only `tests/test_release_tooling_review.py::test_api_shutdown_preserves_shared_runner_and_next_start_reuses_it` against the integrated source.

Before updating the fixture: **1 failed in 1.35s**. The old fake Popen never bound a socket, and the fixture referenced the real model port 11436. The guarded launcher refused the occupied port and correctly did not instantiate the fake runner.

The fixture now uses a non-model port identifier and replaces the bind probe with an assertion-backed simulation tied to the fake child's lifetime. No real socket is probed. This preserves the real startup lock, hash validation, command construction, readiness loop, and API lifespan while isolating process ownership. Assertions prove API shutdown leaves the child alive, unhealthy-but-occupied reuse cannot launch another child, and healthy reuse also preserves exactly one child.

After the fixture repair: **1 passed in 1.26s**; [JUnit evidence](runner-release-reuse.xml). No production code changed. No model requests were issued and no existing process was stopped. The initial RED run reached the old fixture's real bind-availability probe; the repaired test does not touch model ports.
