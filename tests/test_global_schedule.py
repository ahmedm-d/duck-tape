"""Scheduled job definition and secret boundary; no Databricks requests."""
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).parents[1]


def notebook():
    path = ROOT / 'integrations/global/scheduled_notebook.py'
    spec = importlib.util.spec_from_file_location('public_schedule', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.CODE_ROOT = ROOT
    return module


def test_job_is_paused_serial_bounded_and_serverless():
    definition = json.loads((ROOT / 'integrations/global/databricks-job.json').read_text())
    assert definition['schedule']['pause_status'] == 'PAUSED'
    assert definition['max_concurrent_runs'] == 1
    assert definition['timeout_seconds'] == 900
    assert not definition.get('job_clusters')
    task = definition['tasks'][0]
    assert not any(key in task for key in ['new_cluster', 'existing_cluster_id', 'job_cluster_key'])
    assert task['max_retries'] == 0
    assert task['notebook_task']['base_parameters']['publish'] == 'false'
    assert 'warehouse-token' not in json.dumps(definition)


@pytest.mark.parametrize('publish', ['false', 'true'])
def test_notebook_secrets_are_scoped_restored_and_not_returned(monkeypatch, publish):
    module = notebook()
    parameters = {'host': 'https://dbc-a762c421-e9f8.cloud.databricks.com',
                  'warehouse_id': '710499d60f04f73d', 'secret_scope': 'ducktape-public', 'warehouse_auth': 'secret', 'publish': publish}
    reads = []
    def get_secret(scope, key):
        reads.append((scope, key))
        return 'test-secret-' + key
    widgets = SimpleNamespace(text=lambda *args: None, get=lambda key: parameters[key])
    dbutils = SimpleNamespace(widgets=widgets, secrets=SimpleNamespace(get=get_secret))
    def run(sources, *, publish):
        assert len(sources) == 2 and sources[0]['kind'] == 'osv'
        assert sources[1]['record']['source_quality'] == 'unreviewed'
        assert os.environ['STEWARD_DATABRICKS_TOKEN'] == 'test-secret-warehouse-token'
        assert os.environ['STEWARD_GLOBAL_ATLAS_ENABLED'] == ('1' if publish else '0')
        return {'ingestion_failed': 0, 'curation': {'failed': 0}, 'publication': {'failed': 0}}
    monkeypatch.setattr(module.importlib, 'import_module', lambda name: SimpleNamespace(run=run))
    monkeypatch.setenv('STEWARD_DATABRICKS_TOKEN', 'original')
    result = module.run_scheduled(dbutils)
    assert reads == [('ducktape-public', 'warehouse-token')] + ([('ducktape-public', 'public-atlas-uri')] if publish == 'true' else [])
    assert os.environ['STEWARD_DATABRICKS_TOKEN'] == 'original'
    assert 'test-secret' not in json.dumps(result)


def test_notebook_secret_failure_is_sanitized(monkeypatch):
    module = notebook()
    parameters = {'host': 'https://dbc-a762c421-e9f8.cloud.databricks.com',
                  'warehouse_id': '710499d60f04f73d', 'secret_scope': 'ducktape-public', 'warehouse_auth': 'secret', 'publish': 'false'}
    def fail(**kwargs):
        raise RuntimeError('private-secret-do-not-expose')
    dbutils = SimpleNamespace(widgets=SimpleNamespace(text=lambda *args: None, get=lambda key: parameters[key]),
                              secrets=SimpleNamespace(get=fail))
    with pytest.raises(RuntimeError) as error:
        module.run_scheduled(dbutils)
    assert 'private-secret' not in str(error.value)


def test_notebook_native_auth_needs_no_warehouse_secret(monkeypatch):
    module = notebook()
    parameters = {'host': 'https://dbc-a762c421-e9f8.cloud.databricks.com',
                  'warehouse_id': '710499d60f04f73d', 'secret_scope': 'ducktape-public',
                  'warehouse_auth': 'notebook', 'publish': 'false'}
    config = SimpleNamespace(host=parameters['host'], authenticate=lambda: {'Authorization': 'Bearer ephemeral-notebook-token'})
    def run(sources, *, publish):
        assert os.environ['STEWARD_DATABRICKS_TOKEN'] == 'ephemeral-notebook-token'
        return {'ingestion_failed': 0, 'curation': {'failed': 0}, 'publication': {'state': 'not_requested'}}
    monkeypatch.setattr(module.importlib, 'import_module', lambda name: SimpleNamespace(WorkspaceClient=lambda: SimpleNamespace(config=config))
                        if name == 'databricks.sdk' else SimpleNamespace(run=run))
    dbutils = SimpleNamespace(widgets=SimpleNamespace(text=lambda *args: None, get=lambda key: parameters[key]),
                              secrets=SimpleNamespace(get=lambda **kwargs: pytest.fail('Secret read was not requested')))
    assert module.run_scheduled(dbutils)['ingestion_failed'] == 0
