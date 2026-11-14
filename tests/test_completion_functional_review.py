"""Independent failure reproductions; deterministic transport, no live inference/execution."""
import json

import pytest
from fastapi.testclient import TestClient

from backend import db, knowledge, providers, repository, summaries, worker, workflow
from backend.contracts import ProviderResult
from backend.main import app
from test_chunk_retrieval import register, store
from test_live_pipeline_contract import generic


def test_api_approve_first_requires_review_of_generated_plan(generic):
    _, calls, _ = generic
    client = TestClient(app)
    response = client.post('/api/jobs', json={
        'repo_id': 'generic', 'title': 'Fix value',
        'description': 'Return 2 and preserve function name.', 'type': 'feature',
    })
    assert response.status_code == 200, response.text
    job = response.json()
    if job['status'] == 'Awaiting approval':
        assert client.post(f"/api/jobs/{job['id']}/approve", json={}).status_code == 200
    worker.process_job(job['id'])
    current = db.get('jobs', job['id'])
    assert current['status'] == 'Awaiting approval', current
    assert current['plan_hash']
    assert not any(call.task == 'implementation' for call in calls)


def test_three_conflicting_policies_leave_selectable_decision(generic):
    job, _, _ = generic
    for identity, value in [('one', 'integer'), ('two', 'float'), ('three', 'decimal')]:
        db.put('knowledge', {
            'id': identity, 'repo_id': 'generic', 'status': 'team-approved',
            'title': identity, 'content': value, 'scope': 'app.py', 'version': 1,
            'constraint': {'key': 'money', 'value': value},
        })
    for _ in range(4):
        worker.process_job(job['id'])
        current = db.get('jobs', job['id'])
        if current['status'] != 'Needs input':
            assert current['status'] == 'Ready for review', current
            return
        decision = db.get('decisions', current['decision_id'])
        assert len(decision['options']) >= 2, decision
        workflow.transition(job['id'], 'decide', {
            'decision_id': decision['id'], 'option': decision['options'][0]['id'],
        })
    raise AssertionError('Conflict choices did not converge')


def test_changed_policy_invalidates_waiting_preparation_approval(generic):
    job, calls, _ = generic
    db.mutate('jobs', job['id'], lambda item: {
        **item, 'requires_approval': True, 'preparation_approved': False,
    })
    worker.process_job(job['id'])
    assert db.get('jobs', job['id'])['status'] == 'Awaiting approval'
    knowledge.create({
        'repo_id': 'generic', 'title': 'New implementation rule',
        'content': 'Add input validation while changing this function.',
        'scope': 'app.py', 'status': 'team-approved',
    })
    current = db.get('jobs', job['id'])
    assert current['status'] != 'Awaiting approval', current
    assert not current.get('preparation_approved')


def test_summary_cache_changes_with_actual_summary_model(store, monkeypatch):
    snapshot = register(store, 'summary-repo', {'module.py': 'def target(): return 1\n'})
    monkeypatch.setattr(providers, 'health', lambda: {'available': True, 'model': 'main-model'})
    monkeypatch.setenv('STEWARD_MODEL', 'main-model')
    monkeypatch.setenv('STEWARD_SMALL_MODEL', 'summary-a')
    calls = []

    def complete(request, **kwargs):
        model, _ = providers.route(request.task, request.risk, 100, request.scope)
        calls.append(model)
        return ProviderResult(output={
            'summary': f'Summary from {model}',
            'evidence': [{'path': 'module.py', 'start_line': 1, 'end_line': 1}],
            'missing': [],
        }, telemetry={'model': model, 'usage': None})

    monkeypatch.setattr(providers, 'complete', complete)
    first = summaries.build('summary-repo', snapshot['id'])
    assert {item['model'] for item in first} == {'summary-a'}
    previous_calls = len(calls)
    monkeypatch.setenv('STEWARD_SMALL_MODEL', 'summary-b')
    second = summaries.build('summary-repo', snapshot['id'])
    assert len(calls) > previous_calls
    assert {item['id'] for item in first}.isdisjoint(item['id'] for item in second)
    assert {item['model'] for item in second} == {'summary-b'}


def test_planning_context_request_retrieves_more_evidence(generic, monkeypatch):
    job, calls, complete = generic
    requested = False

    def needs_context(request, cancel=None):
        nonlocal requested
        if request.task == 'planning' and not requested:
            requested = True
            calls.append(request)
            return ProviderResult(output={'request_context': 'app.py value'},
                                  telemetry={'attempts': 1, 'usage': None, 'mode': 'test'})
        return complete(request, cancel)

    monkeypatch.setattr(providers, 'complete', needs_context)
    worker.process_job(job['id'])
    current = db.get('jobs', job['id'])
    assert current['status'] == 'Ready for review', current
    assert len([call for call in calls if call.task == 'planning']) == 2


def test_generic_revision_keeps_notes_private_and_binds_previous_patch(generic):
    job, calls, _ = generic
    db.put('notes', {'job_id': job['id'], 'text': 'PRIVATE-TEAM-NOTE-CANARY', 'kind': 'team-note'})
    db.put('knowledge', {'id': 'approved-rule', 'repo_id': 'generic', 'status': 'team-approved',
                         'title': 'Policy', 'content': 'APPROVED-POLICY-CANARY', 'scope': 'app.py', 'version': 1})
    worker.process_job(job['id'])
    first = db.get('jobs', job['id'])
    assert first['status'] == 'Ready for review', first
    revision = db.get('revisions', first['current_revision'])
    first_requests = len(calls)
    workflow.transition(job['id'], 'revise', {'revision_id': revision['id'], 'text': 'EXPLICIT-REVISION-CANARY'})
    worker.process_job(job['id'])
    current = db.get('jobs', job['id'])
    assert current['status'] == 'Ready for review', current
    assert current['current_revision'] != revision['id']
    assert current['input_fingerprint'] != first['input_fingerprint']
    request = next(call for call in calls[first_requests:] if call.task == 'implementation')
    assert request.payload['previous_revision']['patch_hash'] == revision['patch_hash']
    assert 'EXPLICIT-REVISION-CANARY' in json.dumps(request.payload)
    assert 'APPROVED-POLICY-CANARY' in json.dumps(request.payload)
    assert all('PRIVATE-TEAM-NOTE-CANARY' not in json.dumps(call.payload) for call in calls)


def test_resumed_stages_invalidate_changed_provider_model(generic, monkeypatch):
    job, calls, _ = generic
    monkeypatch.setenv('STEWARD_PLANNING_MODEL', 'planner-a')
    db.mutate('jobs', job['id'], lambda item: {
        **item, 'requires_approval': True, 'preparation_approved': False,
    })
    worker.process_job(job['id'])
    assert db.get('jobs', job['id'])['status'] == 'Awaiting approval'
    previous_calls = len(calls)
    monkeypatch.setenv('STEWARD_PLANNING_MODEL', 'planner-b')
    workflow.transition(job['id'], 'approve', {})
    worker.process_job(job['id'])
    assert any(call.task == 'planning' for call in calls[previous_calls:]), [call.task for call in calls[previous_calls:]]


@pytest.mark.parametrize('pattern', [
    '{ value }: { value: () => number }',
    '{ original: value }: Shape',
    '{ nested: { value } }: Shape',
    '[value]: Array<() => number>',
    '{ value = () => 2 }: Shape',
])
def test_ts_destructured_parameter_does_not_resolve_to_import(tmp_path, pattern):
    (tmp_path/'helper.ts').write_text('export function value() { return 1; }\n')
    (tmp_path/'main.ts').write_text(
        'import { value } from "./helper";\n'
        f'function run({pattern}) {{ return value(); }}\n'
        'value();\n'
    )
    index = repository.index_repository(tmp_path)
    calls = [edge for edge in index['edges'] if edge['kind'] == 'calls']
    assert [(edge['source'], edge['target'], edge['line']) for edge in calls] == [('main.ts', 'helper.ts', 3)]
