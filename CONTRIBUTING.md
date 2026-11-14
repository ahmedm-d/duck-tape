# Working together on Duck Tape

We have three lanes: **A models**, **B repository knowledge**, **C global knowledge**; B and C both work on frontend and backend. File ownership and the handoff contract are in [AGENTS.md](AGENTS.md) and [TEAM_PLAN.md](docs/hackathon/TEAM_PLAN.md). Copy the appropriate [starting prompt](docs/hackathon/START_HERE.md) into your coding assistant.

**Team handoff:** use the reviewed foundation on `main` as the common starting point. Fetch the latest `origin/main` before creating each developer's branch/worktree; keep local environment files and runtime databases separate.

## Branches and integration

Start each task from current main in your own clone or worktree:

```sh
git fetch origin
git worktree add ../duck-tape-agent -b feat/agent/live-repair origin/main
```

Use feat/repo/<task> or feat/global/<task> for the other lanes; fix and chore prefixes are also fine. Each checkout has its own local .env and data directory. Do not share runtime databases between checkouts. Use a different run.py --port when running two apps.

Commit only your files, using explicit paths. Open small PRs early and list shared-contract changes. One teammate reviews; the current integration captain integrates and checks the complete flow. B starts as captain; B/C can explicitly hand off that responsibility. Update your private task branch from main with merge/rebase before review, but never rewrite a branch someone else uses. Never force-push main or reset team history. Resolve conflicts with the file owner. Keep unrelated work out of the PR.

Recommended GitHub settings: protect main, require the deterministic check and Commit conventions, require one approving review, disable force pushes and deletion. These settings are **not automatically enforced by these files**; enable them when the account plan permits. Do not invent CODEOWNERS accounts: put the real three handles there once assigned.

## Commit subjects

Use Conventional Commit shape with a scope and an imperative subject beginning with lowercase, maximum 72 characters:

```text
feat(agent): validate Adaption responses before editing
feat(repo): retrieve evidence from the current Atlas snapshot
feat(global): publish curated advisory revisions
fix(ui): show failed regression checks before approval
test(api): reject secret provider settings
docs(hackathon): record measured demo results
```

Allowed types: feat, fix, docs, test, refactor, perf, build, ci, chore, revert.
Use a component scope such as agent, repo, global, data, ui, api, deps or hackathon. A breaking contract uses ! before the colon and an explanation in the body. No WIP/update/final/fix stuff subjects. The original root Initial commit remains historical.

Optional local enforcement (uses Python on PATH):

```sh
git config --local core.hooksPath .githooks
python scripts/check_commit_message.py --subject "feat(agent): add response validation"
```

On macOS/Linux, make the hook executable with `chmod +x .githooks/commit-msg` before enabling it; preserve that executable bit when the team later commits the hook.

CI validates PR titles and all commits introduced by the PR. If squash merging, keep the approved Conventional Commit title.

## Style and structure

- Python: four spaces, snake_case functions/modules, PascalCase classes, type annotations on new interfaces; small modules and explicit errors.
- React/TypeScript: two spaces, PascalCase components, typed props/API boundaries; state/loading/error/keyboard states must work. Add components under frontend/src/components instead of expanding the central file unnecessarily.
- Reuse backend/contracts.py and existing db/provider/retrieval helpers. No parallel abstractions, shell-interpolated commands, or general refactor during the hackathon.
- .editorconfig specifies UTF-8, LF and indentation. Preserve surrounding legacy style; do not reformat untouched files.
- A owns inference and worker behavior; B owns repository understanding, retrieval and local applicability; C owns public global knowledge, signals and cloud adapters. B/C share frontend through explicit file handoffs.
- Before touching main.tsx, shared CSS/API types, routes, config or shared tests, claim exact files in the PR/team handoff. Finish a small commit/PR and state changed contracts/checks, then release ownership; the next person syncs before taking over. Independent components can proceed in parallel. B is initial captain for shared backend/config/docs; the role can be handed to C.
- Use the planned [knowledge contract](docs/hackathon/KNOWLEDGE_CONTRACT.md): private profiles and matching stay local; public catalogue revisions remain separate. Existing repository knowledge scope global is not the shared catalogue.
- Runtime state belongs in ignored data/, secrets in ignored .env or the server environment. Never put keys in VITE_* variables.

## Verification

```sh
python scripts/setup.py
.venv/bin/python -m pytest tests/test_sponsor_providers.py tests/test_data_platforms.py tests/test_hackathon_foundation.py -q
.venv/bin/python scripts/acceptance.py --tier deterministic
```

On Windows use .venv/Scripts/python. The setup builds the frontend; browser checks also need Playwright Chromium (cd frontend && npx playwright install chromium) or STEWARD_BROWSER_EXECUTABLE pointing to installed Chrome. Atlas dependencies are pinned in requirements.lock. Normal startup requires the private Atlas configuration; deterministic gates explicitly set STEWARD_ATLAS_REQUIRED=0 and never qualify the live integration.

Report test scope honestly. Mocked HTTP is a contract test, not live integration. Record model ID, provider, snapshot, check results, latency and known usage for real runs. Never claim training or a measured improvement from a health probe.

Every PR states the behavior change, actual checks, remaining uncertainty and files handed to another lane. Before submission, the current integration captain runs the whole deterministic gate and the demo on the exact commit. Preserve creation provenance and confirm eligibility of existing code with organisers.
