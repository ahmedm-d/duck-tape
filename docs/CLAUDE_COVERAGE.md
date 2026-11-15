# Claude capability completion

Claude substitutes prompted specialist roles, not trained Adaption weights. Existing approval, snapshot, sandbox, revision and cancellation gates remain mandatory.

- [x] Route all 11 native roles to Claude, preserving structured controllers.
- [x] Qualify a complete bug-to-patch-to-check journey with specialist mode.
- [x] Add durable public GitHub PR review bound to base/head commits.
- [x] Add bounded source security review with validated citations and explicit hypotheses.
- [x] Preserve OSV matching as deterministic known-advisory evidence.
- [x] Expose review actions, results, retry/cancel and source references in the existing UI.
- [x] Test boundaries, independent review, build and live checks.

Reviews are read-only and never post approval to GitHub. Public GitHub reads use anonymous quotas; private PR access is not included. Whole-repository security completeness is not claimed. Runtime proof continues to require sandbox checks.

## Configuration and demo

Use the server-side Anthropic key with `STEWARD_MODEL_PROVIDER=anthropic`, `STEWARD_MODEL_ENDPOINT=https://api.anthropic.com`, an available Claude ID in `STEWARD_MODEL` and `STEWARD_SMALL_MODEL`, and `STEWARD_SPECIALIST_MODE=1`. Tested context limit: 128000; request timeout: 120 seconds. Credentials stay in ignored `.env`. Mode `0` retains the structured controller alone.

1. Create a bug request and approve its plan. Inspect the versioned diff, regression checks and explanation before applying.
2. Open repository Reviews, then **Model reviews**, and start a security review. Inspect citations and hypothesis labels.
3. On an open public PR, choose **AI review #N**, then **Review PR #N**. Reload to see persisted results; failed/running operations expose retry/cancel.
4. GitHub publication and merging remain separate actions.

The verified local checkout is `C:/Users/ahmed/Projects/duck-tape-integration-20261115`, served at `http://127.0.0.1:8783`. Restart from that directory with `C:/Users/ahmed/Projects/ducktape/steward/.venv/Scripts/python.exe run.py --port 8783`. Other worktrees and ports were preserved.

## Evidence and limits

- Focused backend suite: 126 passed; context-budget follow-up: 25 passed.
- Browser review/auth suite: 24 passed; final model-review suite: 3 passed, including exact PR head submission.
- Full acceptance production build, supervised restart and 63 browser tests passed. The one-shot backend gate hit its 600-second deadline after 766 passes and one skip, without assertion failures. Remaining tests were run separately; the report preserves this timeout rather than labelling the whole gate green.
- Live Python bug qualification passed with `claude-opus-5-5`: 11 calls, 8,431 output tokens, zero repair rounds, Docker verification, holdout checks and managed application. Repair remains covered deterministically; this successful live run needed none.
- Live source-security and public PR reviews completed with Claude and validated source citations. These reviews execute no checks and publish no GitHub approval.
- All 11 roles exercised through the live job/reviews and auxiliary inference probes. DB/IaC initially hit a 2,048-token probe cap, then passed with concise requests and 4,096 tokens. Auxiliary proposals were not deployed.
- Independent review findings were fixed, including origin identity, snapshot publication, citation isolation, repeat-review deduplication and context budgeting. Final static recheck found no blocking defect.

[Live evidence](evidence/claude-expansion-20261115.json) retains failed development attempts as well as the successful run. The known-advisory pipeline was preserved, not requalified by this expansion.

Specialist jobs have a bounded 32-call/64,000-output-token budget, eight tool turns and two repair rounds. Controller-only limits are unchanged. PR reviews accept at most 50 changed files and bounded text. Oversized context is trimmed with explicit omissions and updated citation ranges; a single oversized line may need a larger context limit. Deleted/unsupported files are disclosed. Team notes and draft policies stay outside prompts. PR source is bound to the immutable head, without mixing local snapshot source.

Not verified here: private PR access, trained Adaption serving, or the full multi-language live qualification matrix. Security findings remain hypotheses, not exhaustive vulnerability detection.

The deterministic harness now isolates model, embedding and data-directory configuration from the live `.env`. Native-adapter tests explicitly restore their own automatic routing configuration; no checks or approval gates were weakened.

Backend coverage across the completed prefix, remaining-test batch and seven-test handoff recheck: **945 unique tests passed, one skipped**. Four native fixture failures were resolved by isolating their routing flag. This is partitioned evidence, not a single-command acceptance pass.
