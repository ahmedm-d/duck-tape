> Historical training handoff. Do not apply its worker patch or treat its local-only graph/reachability claims as current. Runtime integration already exists. Use the [current graph contract](../../docs/REPO_GRAPH_HANDOFF.md) and `backend/model_schemas.py`. Adaption training is underway; completed artifacts still need live qualification.

# Worker Handoff Patch — `HANDOFF_B_TO_A.md` Section 5 Wiring

This document contains the exact patch snippets for `backend/worker.py` (and `tests/test_worker.py`) implementing Section 5 of `HANDOFF_B_TO_A.md`:
1. Switching `retrieve(repo['index'], ...)` to the snapshot-bound form `retrieve(repo['id'], repo.get('source_snapshot_id') or repo['snapshot'], query, ...)` (lines 182, 207, 376, 500, 608).
2. Passing `job['applicability']` (and `job['catalogue']` if present) into the job payload sent to the model (lines 188 and 379).
3. Calling `agent_graph_context(...)` (from `backend/agent_context.py` once merged from `feat/repo/applicability`) for `investigation`, `planning`, `implementation`, and `review` tasks and formatting it in the model prompt as `<graph_context>…</graph_context>`.
4. Regression unit tests proving team notes (`db.put('notes', ...)`) never reach model prompts, while `<graph_context>` and `job['applicability']` do.

> **Note:** `backend/worker.py` is not modified directly in this dataset-migration commit; apply this patch when integrating B's `backend/agent_context.py` on `feat/agent/worker-handoff` (or after `feat/repo/applicability` merges into `main`).

---

## 1. Exact Changes for `backend/worker.py`

### A. `_process_legacy_fixture` (lines 182, 188, 207)

Replace lines 182–188:
```python
        snapshot_ref = repo.get('source_snapshot_id') or repo['snapshot']
        context = retrieve(repo['id'], snapshot_ref, job['title'] + ' ' + job['description'], knowledge=knowledge)
        context.update(snapshot=repo['snapshot'], repo_id=repo['id'], overview=repo['index'].get('overview', ''))
        knowledge = context.get('knowledge', [])
        run = db.put('runs', {**run, 'context': context})
        files = {f['path']: f['content'] for f in repo['index']['files'] if f.get('status') == 'indexed' and isinstance(f.get('content'), str)}
        fixture = bool(repo.get('is_sample') and job.get('scenario') in {s[0] for s in sample.SCENARIOS})
        graph_ctx = _build_graph_context_block(repo['id'], snapshot_ref, job['title'] + ' ' + job['description'], job=job)
        payload = {
            'task': 'implementation',
            'job': {k: job.get(k) for k in ('id', 'title', 'description', 'revision_instructions', 'current_revision', 'applicability')},
            'snapshot': repo['snapshot'],
            'context': context,
            'graph_context': graph_ctx,
            'approved_decisions': decisions,
            'approved_knowledge': knowledge,
        }
```

And at line 207 (additional retrieval round):
```python
                additional = retrieve(
                    repo['id'],
                    snapshot_ref,
                    query,
                    knowledge=[k for k in db.list_records('knowledge') if k.get('repo_id') == repo['id'] and k.get('status') == 'team-approved'],
                )
```

### B. `process_job` (lines 376, 379, plus `investigation`, `planning`, `implementation`, `review` stages)

Add helper inside `backend/worker.py` (gracefully handles `backend.agent_context` when merged, or raises/falls back cleanly):
```python
def _format_graph_context(repo_id: str, snapshot_id: str, query: str, *, job: dict | None = None, budget_tokens: int = 3500) -> str:
    """Call B's agent_graph_context(...) and wrap as <graph_context>...</graph_context>."""
    from .agent_context import agent_graph_context
    ctx = agent_graph_context(repo_id, snapshot_id, query, budget_tokens=budget_tokens, job=job)
    return f"<graph_context>\n{json.dumps(ctx, ensure_ascii=False, separators=(',', ':'))}\n</graph_context>"
```

Replace lines 376–382:
```python
        snapshot_ref = repo.get('source_snapshot_id') or repo['snapshot']
        context = retrieval.retrieve(repo['id'], snapshot_ref, job['title'] + ' ' + job['description'], knowledge=approved)
        context.pop('latency_ms', None)
        context.update(snapshot=repo['snapshot'], repo_id=repo['id'])
        seed = {
            'job': {key: job.get(key) for key in ('id', 'title', 'description', 'base_snapshot', 'current_revision', 'revision_instructions', 'retry_generation', 'applicability')},
            'source_snapshot_id': source_id,
            'knowledge': approved,
            'decisions': [{key: record.get(key) for key in ('id', 'selected', 'selected_at', 'author', 'rationale', 'constraint_versions')} for record in decisions if record.get('selected')],
        }
```

And at lines 492–510 (`investigation` and `planning`), pass `<graph_context>` wrapped prompt context:
```python
        graph_query = job['title'] + ' ' + job['description']
        graph_context_block = _format_graph_context(repo['id'], snapshot_ref, graph_query, job=job)
        base_payload = {
            'job': seed['job'],
            'context': context,
            'graph_context': graph_context_block,
            'approved_knowledge': approved,
            'approved_decisions': [record for record in decisions if record.get('selected')],
            'snapshot': source_id,
        }
        investigation = call('investigation', base_payload, 'investigation')
        save(investigation=investigation)
        plan = call('planning', {**base_payload, 'investigation': investigation}, 'planning')
        evidence_rounds = 0
        while 'request_context' in plan and evidence_rounds < 2:
            evidence_rounds += 1
            query = plan['request_context']
            additional = retrieval.retrieve(repo['id'], snapshot_ref, query, knowledge=approved)
            additional.pop('latency_ms', None)
            items = {(item['path'], item['start_line'], item['end_line']): item for item in context['items'] + additional['items']}
            context['items'] = list(items.values())
            context['additional_queries'] = context.get('additional_queries', []) + [query]
            paths |= {item['path'] for item in additional['items']}
            if not constraints(paths):
                return
            graph_context_block = _format_graph_context(repo['id'], snapshot_ref, f"{graph_query}\n{query}", job=job)
            base_payload.update(context=context, graph_context=graph_context_block, approved_knowledge=approved)
            save(context=context, evidence_rounds=evidence_rounds)
            plan = call('planning', {**base_payload, 'investigation': investigation}, 'planning-context-' + str(evidence_rounds))
```

And at line 608 (implementation additional evidence round):
```python
                additional = retrieval.retrieve(repo['id'], snapshot_ref, query, knowledge=approved)
```
And update `payload` with refreshed `graph_context`:
```python
                payload.update(
                    context=compact_context(),
                    graph_context=_format_graph_context(repo['id'], snapshot_ref, f"{graph_query}\n{query}", job=job),
                    approved_knowledge=approved,
                )
```

And at lines 703–706 (`review` stage), include `graph_context` in `review_payload`:
```python
                    review_payload = {
                        'job': seed['job'],
                        'graph_context': graph_context_block,
                        'approved_knowledge': approved,
                        'approved_decisions': payload['approved_decisions'],
                        'proposed_sources': [{'path': item['path'], 'content': item['after']} for item in candidate.files if not item['binary']],
                        'checks': result,
                        'patch': candidate.diff,
                        'plan': plan,
                        'acceptance_coverage': acceptance_coverage,
                    }
```

*(Note: in `backend/providers.py`, include `payload['graph_context']` directly in the user message content when constructing prompts for `investigation`, `planning`, `implementation`, `repair`, and `review`, so `<graph_context>…</graph_context>` is preserved verbatim.)*

---

## 2. Unit Test (`tests/test_worker.py`): Team Notes Never Reach Model + `<graph_context>` & `applicability` Do

```python
def test_worker_passes_graph_context_and_applicability_but_excludes_team_notes(workspace, monkeypatch):
    """Verify HANDOFF_B_TO_A Section 5: team notes never reach model; graph_context & applicability do."""
    import sys
    import types
    from provider_fixture import adapt_legacy_provider, run_with_plan_approval

    applicability_record = {
        'kind': 'applicability_match',
        'version': 1,
        'status': 'matched',
        'entry_id': 'GHSA-demo-0001',
        'entry_revision': 1,
        'citations': [{'path': 'requirements.lock', 'start_line': 1, 'end_line': 1, 'hash': '0' * 64}],
        'limitations': ['A version match establishes affected-version applicability, not exploitability.'],
        'cwe': 'CWE-20',
        'sink_reachable': None,
    }
    repo = db.mutate('repositories', workspace['id'], lambda r: {**r, 'is_sample': False})
    job = db.mutate(
        'jobs',
        queued('bug')['id'],
        lambda j: {**j, 'applicability': applicability_record},
    )
    secret_note = 'PRIVATE TEAM NOTE: DO NOT SEND TO MODEL OR EMBEDDINGS'
    db.put('notes', {'job_id': job['id'], 'repo_id': repo['id'], 'text': secret_note})

    # Stub B's backend.agent_context.agent_graph_context until feat/repo/applicability merges
    fake_agent_context = types.ModuleType('backend.agent_context')
    fake_agent_context.agent_graph_context = lambda repo_id, snapshot_id, query, budget_tokens=3500, job=None: {
        'scope': {'repo_id': repo_id, 'snapshot_id': snapshot_id, 'engine': 'graphify', 'engine_version': '0.9.108', 'graph_hash': 'a' * 32},
        'focal_symbols': [],
        'edges': [],
        'blast_radius': {'dependent_files': ['pricing.py'], 'communities_touched': 1, 'linked_test_files': ['tests/test_quote.py'], 'structural_risk': 'low', 'basis': 'AST test fixture'},
        'catalogue_matches': [job['applicability']] if job and job.get('applicability') else [],
        'approved_knowledge': [],
        'gaps': ['Runtime sink reachability analysis unavailable in v1'],
    }
    monkeypatch.setitem(sys.modules, 'backend.agent_context', fake_agent_context)

    captured_calls = []

    def complete(task, payload, risk):
        captured_calls.append((task, payload))
        serialized = json.dumps(payload)
        assert secret_note not in serialized
        if task == 'review':
            return {'approved': True, 'findings': []}, {'model': 'test transport', 'usage': None}
        return {
            'edits': [{'path': 'pricing.py', 'content': 'def total(items, tax_rate):\n    return 0\n'}],
            'explanation': 'Handoff verification fixture',
        }, {'model': 'test transport', 'mode': 'test', 'usage': None}

    monkeypatch.setattr(providers, 'complete', complete)
    adapt_legacy_provider(monkeypatch)
    typed_complete = providers.complete

    def capture_typed(request, cancel=None):
        captured_calls.append((request.task, request.payload))
        assert secret_note not in json.dumps(request.payload)
        return typed_complete(request, cancel=cancel)

    monkeypatch.setattr(providers, 'complete', capture_typed)
    run_with_plan_approval(job['id'])

    assert captured_calls, 'Expected worker to invoke provider.complete'
    stages_seen = {task for task, _ in captured_calls}
    assert {'investigation', 'planning', 'implementation', 'review'}.issubset(stages_seen)

    for task, payload in captured_calls:
        if task in ('investigation', 'planning', 'implementation', 'review'):
            gc = payload.get('graph_context', '')
            assert gc.startswith('<graph_context>') and gc.endswith('</graph_context>')
            assert '"sink_reachable":null' in gc
            assert payload['job'].get('applicability') == applicability_record
        assert secret_note not in json.dumps(payload)

    assert secret_note not in json.dumps(db.list_records('runs'))
```
