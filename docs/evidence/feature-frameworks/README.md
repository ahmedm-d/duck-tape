# Actual feature framework acceptance

The fixed reviewed fixtures passed all three actual Docker cases: **3 passed in 453.90 seconds**. No model calls or provider-response fixtures were used. [Full JUnit](results.xml) records each case; [compact evidence](report.json) includes raw target/negative logs, all check phases, runtime images, dependency fingerprints, verified artifact hashes, exact manifests, source identities and links/hashes for the larger original reports retained under `data/live-feature-frameworks/`.

| Framework | Runtime | Full case duration | Selected target | Deliberately skipped target |
| --- | --- | ---: | --- | --- |
| pytest 8.3.5 | Python 3.12.11 | 37.462s | 1 test, 0 skipped; observed | Rejected |
| Jest 29.7.0 | Node 22.16.0 | 244.332s | 3 tests, 2 skipped; exact target observed | Rejected |
| Vitest 2.1.9 | Node 22.16.0 | 171.595s | 3 tests, 2 skipped; exact target observed | Rejected |

Every fixture has an exact target, a distinct similarly named passing test and an unrelated deliberate skip. The controller-selected target ran once, only in the candidate phase. Baseline and candidate suites remained separate, and the test-only regression ran against unchanged implementation. A second set of actual environments deliberately skipped the mapped target; it could not borrow unrelated passing evidence. Jest/Vitest report unselected tests as skipped, so the valid target is established by its exact passing marker as well as the executed count.

The initial actual Jest run reproduced two errors: global skipped-count rejection blocked an exact passing target, and the verifier counted the passed suite as another test (4 instead of 3). The historical RED, including its raw logs and original report hash/path, is retained in `report.json`. After that evidence was saved, the owned slow pytest tree and its verified labeled containers were stopped; zero owned active containers remained. No completed old Vitest run is claimed. Focused regressions then failed before the fixes and passed afterward. The final regression set, including the independent completion boundary tests, passed **36 tests in 11.48 seconds**.

The real run also exposed excessive setup cost from one Docker invocation per npm cache artifact. Inspection of the pinned npm implementation confirmed that `cache add` accepts multiple specifications through `Promise.all`. Preparation now batches at most **16 already verified absolute tar paths** in one actual npm invocation. Offline mode, disabled scripts, registry/hash validation, resource limits and the subsequent offline `npm ci` remain intact. A 35-artifact contract test first failed, then verified batches of 16/16/3. The final full framework suite's measured 7m33s includes cold acquisition and repeated isolated environments; the release CLI's sandbox deadline must cover this and its other requested suites. Individual runtime/resource limits were preserved.

Reproduce from the project root with installed Python dependencies and permitted Docker access:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
$env:STEWARD_LIVE_SANDBOX='1'
python -m pytest tests/live/test_feature_frameworks.py -q -s -p no:cacheprovider --junitxml=data/live-feature-frameworks-batched-results.xml
python -m pytest tests/test_feature_framework_observation.py tests/test_verification.py tests/test_dependencies.py tests/test_final_completion_acceptance.py -q -p no:cacheprovider
```

The npm fixture bootstrap is metadata-only `npm install --package-lock-only --ignore-scripts --no-audit --no-fund --registry=https://registry.npmjs.org` in a new empty owned directory with the exact reviewed manifest and empty user/global configuration. The test asserts no host `node_modules` is created. Actual packages are subsequently fetched/hash-verified by the existing controller and installed/executed offline in the sandbox. The exact resulting locks are retained beside this report; Python uses the four exact wheel pins in `pytest-requirements.txt`.

Scope: these results qualify the tested pytest selectors and literal root-level Jest/Vitest labels on the recorded runtime images. Nested `describe` full-name adapters are not established by this evidence; unmatched targets fail verification. Native transforms, custom runners and alternate runtime/library combinations need their own supported locked environment. This test does not qualify a model or prove generated tests cannot be deceptive.
