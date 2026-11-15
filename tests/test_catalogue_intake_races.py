"""Catalogue intake rejects stale or cancelled evidence at its write boundary."""
import pytest
from fastapi import HTTPException

from backend import applicability, db, global_pipeline, main
from backend.contracts import CancellationToken, CancelledError
from tests.test_applicability_catalogue import env, fresh


def entry_for(catalogue):
    stored = catalogue.ingest(fresh())
    return applicability.from_global_entry(stored.model_dump(mode='json'))


@pytest.mark.parametrize('engine', [[], {}, None, 1, 'unknown'])
def test_invalid_graph_engine_is_rejected_before_atlas_query(env, monkeypatch, engine):
    api, _, _, _ = env
    monkeypatch.setattr(global_pipeline, 'query_atlas', lambda *a, **kw: pytest.fail('Invalid request disclosed package names'))
    response = api.post('/api/repositories/demo/knowledge-map', json={'provider': 'atlas', 'engine': engine})
    assert response.status_code == 422


def test_cancelled_withdrawal_cannot_invalidate_existing_job(env, monkeypatch):
    _, _, catalogue, _ = env
    entry = entry_for(catalogue)
    [job] = main._applicability_intake('demo', {'entry': entry})['jobs']
    token = CancellationToken()
    original = applicability.intake_payloads

    def cancel_before_invalidation(*args):
        token.cancel()
        return original(*args)

    monkeypatch.setattr(applicability, 'intake_payloads', cancel_before_invalidation)
    with pytest.raises(CancelledError):
        main._applicability_intake('demo', {'entry': {**entry, 'status': 'withdrawn'}}, cancel=token)
    assert db.get('jobs', job['id']) == job


def test_older_withdrawal_preserves_concurrent_newer_revision(env, monkeypatch):
    _, _, catalogue, _ = env
    entry = entry_for(catalogue)
    newer = {**entry, 'revision': entry['revision'] + 1, 'content_hash': 'b' * 64}
    original = applicability.intake_payloads
    inserted = []

    def publish_newer_before_invalidation(found, incoming):
        monkeypatch.setattr(applicability, 'intake_payloads', original)
        inserted.extend(main._applicability_intake('demo', {'entry': newer})['jobs'])
        return original(found, incoming)

    monkeypatch.setattr(applicability, 'intake_payloads', publish_newer_before_invalidation)
    with pytest.raises(HTTPException) as error:
        main._applicability_intake('demo', {'entry': {**entry, 'status': 'withdrawn'}})
    assert error.value.status_code == 409
    assert db.get('jobs', inserted[0]['id']) == inserted[0]


def test_snapshot_change_before_withdrawal_does_not_invalidate_jobs(env, monkeypatch):
    _, _, catalogue, _ = env
    entry = entry_for(catalogue)
    [job] = main._applicability_intake('demo', {'entry': entry})['jobs']
    original = applicability.intake_payloads

    def replace_snapshot_before_invalidation(*args):
        db.mutate('repositories', 'demo', lambda repo: {**repo, 'source_snapshot_id': 'new-snapshot'})
        return original(*args)

    monkeypatch.setattr(applicability, 'intake_payloads', replace_snapshot_before_invalidation)
    with pytest.raises(HTTPException) as error:
        main._applicability_intake('demo', {'entry': {**entry, 'status': 'withdrawn'}})
    assert error.value.status_code == 409
    assert db.get('jobs', job['id']) == job


@pytest.mark.parametrize('revision_delta', [-1, 0])
def test_older_or_conflicting_revision_cannot_create_job(env, revision_delta):
    _, _, catalogue, _ = env
    entry = entry_for(catalogue)
    current = {**entry, 'revision': entry['revision'] + 1, 'content_hash': 'b' * 64}
    [job] = main._applicability_intake('demo', {'entry': current})['jobs']
    incoming = {**entry, 'revision': current['revision'] + revision_delta}
    with pytest.raises(HTTPException) as error:
        main._applicability_intake('demo', {'entry': incoming})
    assert error.value.status_code == 409
    assert db.list_records('jobs') == [job]


def test_other_repository_cannot_block_invalidation(env):
    _, _, catalogue, _ = env
    entry = entry_for(catalogue)
    [job] = main._applicability_intake('demo', {'entry': entry})['jobs']
    other = db.put('jobs', {**job, 'id': 'other-job', 'repo_id': 'other',
                            'catalogue': {**job['catalogue'], 'entry_hash': 'b' * 64}})
    result = main._applicability_intake('demo', {'entry': {**entry, 'status': 'withdrawn'}})
    assert result['invalidated'] == [job['id']]
    assert db.get('jobs', other['id']) == other
