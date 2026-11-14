import pytest
from backend import db, sample


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path)
    db.init()
    return sample.seed_sample()


def test_approved_edit_preserves_version_and_requires_new_approval(repo):
    from backend import knowledge
    entry = knowledge.create({'repo_id': repo['id'], 'title': 'Money', 'content': 'Use integer cents', 'scope': 'pricing.py', 'status': 'team-approved', 'evidence': ['README.md']})
    changed = knowledge.revise(entry['id'], {'content': 'Use decimal values'}, expected_version=1)
    assert changed['status'] == 'observed' and changed['approval_state'] == 'pending'
    assert knowledge.history(entry['id'])[0]['content'] == 'Use integer cents'
    assert knowledge.for_paths(repo['id'], repo['snapshot'], ['pricing.py']) == []
    approved = knowledge.approve(entry['id'], 2, repo['snapshot'])
    assert approved['status'] == 'team-approved'
    assert knowledge.for_paths(repo['id'], repo['snapshot'], ['pricing.py'])[0]['version'] == 3


def test_bad_internal_evidence_is_rejected(repo):
    from backend import knowledge
    with pytest.raises(ValueError, match='range'):
        knowledge.create({'repo_id': repo['id'], 'title': 'Bad', 'content': 'A rule', 'evidence': [{'path': 'pricing.py', 'start_line': 10000, 'end_line': 10001}]})


def test_seed_knowledge_citation_can_be_approved(repo):
    from backend import knowledge
    entry=db.list_records('knowledge')[0]
    approved=knowledge.approve(entry['id'],entry['version'],repo['snapshot'])
    assert approved['evidence'][0]['path']=='README.md'
    assert approved['evidence'][0]['start_line']==3
    assert approved['evidence'][0]['end_line']==4


def test_unrelated_change_preserves_rejection_and_scoped_approval(repo):
    from backend import knowledge, repository
    approved = knowledge.create({'repo_id': repo['id'], 'title': 'Pricing', 'content': 'Use cents', 'scope': 'pricing.py', 'status': 'team-approved', 'evidence': ['pricing.py']})
    rejected = knowledge.create({'repo_id': repo['id'], 'title': 'No', 'content': 'Rejected idea', 'status': 'rejected'})
    from pathlib import Path
    path = Path(repo['root']) / 'README.md'
    path.write_text(path.read_text() + '\nUnrelated note\n')
    current = repository.index_repository(Path(repo['root']))
    knowledge.invalidate_snapshot(repo['id'], repo['index'], current)
    assert db.get('knowledge', rejected['id'])['status'] == 'rejected'
    assert db.get('knowledge', approved['id'])['status'] == 'team-approved'
    path = Path(repo['root']) / 'pricing.py'
    path.write_text(path.read_text() + '\n# changed behavior evidence\n')
    changed = repository.index_repository(Path(repo['root']))
    knowledge.invalidate_snapshot(repo['id'], current, changed)
    assert db.get('knowledge', approved['id'])['status'] == 'stale'


def test_promotion_does_not_approve_policy_and_notes_stay_private(repo):
    from backend import knowledge
    job = db.put('jobs', {'id': 'job', 'repo_id': repo['id'], 'status': 'Ready for review'})
    note = db.put('notes', {'job_id': job['id'], 'text': 'A local preference', 'kind': 'revision-request'})
    promoted = knowledge.promote(job['id'], note['id'])
    assert promoted['status'] == 'observed' and promoted['promoted_from'] == note['id']
    assert not any(x['id'] == promoted['id'] for x in knowledge.for_paths(repo['id'], repo['snapshot'], ['app.py']))


def test_conflicting_structured_constraints_have_explicit_conflict(repo):
    from backend import knowledge
    one = knowledge.create({'repo_id': repo['id'], 'title': 'Limit', 'content': 'Maximum is 100', 'scope': 'app.py', 'status': 'team-approved', 'constraint': {'key': 'order_limit', 'value': 100}})
    two = knowledge.create({'repo_id': repo['id'], 'title': 'Other limit', 'content': 'Maximum is 200', 'scope': 'app.py', 'status': 'team-approved', 'constraint': {'key': 'order_limit', 'value': 200}})
    conflict = knowledge.conflicts(repo['id'], ['app.py'])
    assert {one['id'], two['id']} == set(conflict[0]['entries'])


def test_approval_requires_exact_version_and_snapshot(repo):
    from backend import knowledge
    entry = knowledge.create({'repo_id': repo['id'], 'title': 'Rule', 'content': 'Initial'})
    with pytest.raises(knowledge.Conflict):
        knowledge.revise(entry['id'], {'status': 'team-approved'})
    with pytest.raises(knowledge.Conflict):
        knowledge.approve(entry['id'], 1, 'old')
    knowledge.revise(entry['id'], {'content': 'Changed'}, 1)
    with pytest.raises(knowledge.Conflict):
        knowledge.approve(entry['id'], 1, repo['snapshot'])
    assert db.get('knowledge', entry['id'])['status'] == 'observed'
