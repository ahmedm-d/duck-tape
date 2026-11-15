"""Consumer contract for B's graph producer, independent of its storage engine."""
import pytest

from backend import agent_context, graphify_graph, retrieval
from tests.test_agent_context import parcel


@pytest.mark.parametrize('field,value', [('repo_id', 'other-repository'), ('snapshot_id', 'old-snapshot')])
def test_context_rejects_graph_from_another_scope(parcel, monkeypatch, field, value):
    _, repo = parcel
    build = graphify_graph.builtin
    monkeypatch.setattr(graphify_graph, 'builtin', lambda *a, **kw: {**build(*a, **kw), field: value})
    with pytest.raises(ValueError, match='Graph scope'):
        agent_context.agent_graph_context(repo['id'], repo['source_snapshot_id'], 'quote')


def test_graph_expansion_rejects_stale_cloud_graph(parcel, monkeypatch):
    _, repo = parcel
    graph = graphify_graph.builtin(repo['id'], repo['source_snapshot_id'])
    monkeypatch.setattr(graphify_graph, 'graphify', lambda *a, **kw: {**graph, 'snapshot_id': 'old-snapshot'})
    with pytest.raises(ValueError, match='Graph scope'):
        retrieval.retrieve(repo['id'], repo['source_snapshot_id'], 'quote', graph_engine='graphify')


def test_context_omits_stale_catalogue_match(parcel):
    _, repo = parcel
    job = {'catalogue': {'entry_id': 'OLD-ENTRY', 'snapshot_id': 'old-snapshot'},
           'applicability': {'status': 'matched', 'matched_packages': [], 'citations': []}}
    context = agent_context.agent_graph_context(repo['id'], repo['source_snapshot_id'], 'quote', job=job)
    assert context['catalogue_matches'] == []
    assert any('another snapshot' in gap for gap in context['gaps'])
