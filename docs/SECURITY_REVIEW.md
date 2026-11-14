# Independent security and reliability review

Reviewed 2026-11-14 by the security review agent, separately from the implementer. This is a local prototype review, not a penetration-test certification.

## Findings and fixes

| Severity | Reproduction | Affected implementation | Resolution |
| --- | --- | --- | --- |
| Major | `safe_path(root, '.GIT/config')` accepted a Windows alias of Git administrative configuration; trailing-dot/space aliases and device names also bypassed the original guard. | `backend/worker.py`, `safe_path` | Worker owner added case-insensitive administrative-path rejection, Windows aliases/device-name rejection, and `.env` prefix exclusion. Independent regression cases pass. Git hooks are additionally disabled; configuration writes must still be forbidden because Git filters are executable. No malicious filter was executed during this review. |
| Major | `.AWS/config.json`, `.GIT/notes.md`, and `.ENV.json` were indexed and made available as source evidence. | `backend/repository.py`, `index_repository` | Secret/generated directory and environment-path matching now uses case-insensitive comparisons. Regression confirms content is absent from returned index. |
| Major | JSON `{"api_key": "abcdefghijklmnop123456"}`, TypeScript `export const password = "abcdefghijklmnop123456"`, and `DATABASE_PASSWORD=abcdefghijklmnop123456` were indexed. | `backend/repository.py`, credential detection | Added assignment detection covering quoted JSON keys, declarations, and prefixed secret variable names. All three were reproduced failing before the fix and pass after it. |

## Independent regression evidence

`tests/test_security_review.py` contains 19 cases checking:

- Windows Git metadata, alternate stream, device-name, environment-file and path aliases.
- Archive reserved-name/trailing-dot/trailing-space rejection.
- DNS results containing both public and private addresses are rejected.
- Credential and case-variant secret-path exclusion, with content absence assertions.
- Cancellation while a provider response is pending keeps the terminal state and publishes no revision after the delayed response returns.
- Two simultaneous revision requests produce one accepted transition and one conflict, with one instruction and invalidated approval.
- Deleting implementation and refreshing removes current evidence and invalidates a ready patch's approval.

All 19 independent cases passed after the fixes. Existing repository, API and worker tests supplement this with traversal/symlink checks, loopback/private URL rejection, origin checks, notes outside prompts, real fixture execution and before/after regression evidence. The provider transport in the cancellation test is a deliberate mock; it does not claim live model inference.

## Boundaries and limitations

Imported and live-generated code has no configured execution sandbox and must remain analysis/export-only. The fixture runner executes fixed reviewed sample code only. The local workspace's demo authors are labels, not access control. No paid service, privileged setup, remote push or host security setting was used.

Credential filtering is conservative pattern matching, not a proof that arbitrary source text contains no secret. Obfuscated, unknown-format, or otherwise unrecognized secrets cannot be reliably identified by a prototype scanner; inspect imported material before configuring an external inference endpoint. Source and ticket text are untrusted prompt evidence; executable permissions are enforced by code, not by model agreement. No live provider adversarial evaluation was possible in this review.
