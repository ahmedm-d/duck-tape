# Databricks notebook source
"""Paused-job entry point. Secrets are resolved at runtime, never saved as parameters."""
import importlib
import json
import os
from pathlib import Path
import sys

CODE_ROOT = Path('/Workspace/Shared/duck-tape-public')


def run_scheduled(dbutils) -> dict:
    defaults = {'host': 'https://dbc-a762c421-e9f8.cloud.databricks.com',
                'warehouse_id': '710499d60f04f73d', 'secret_scope': 'ducktape-public',
                'warehouse_auth': 'notebook', 'publish': 'false'}
    for name, value in defaults.items():
        dbutils.widgets.text(name, value)
    settings = {name: dbutils.widgets.get(name) for name in defaults}
    environment = {}
    previous = {}
    try:
        if settings['publish'] not in {'true', 'false'}:
            raise ValueError('Invalid publication setting')
        publish = settings['publish'] == 'true'
        if settings['warehouse_auth'] == 'notebook':
            workspace = importlib.import_module('databricks.sdk').WorkspaceClient()
            if workspace.config.host.rstrip('/') != settings['host'].rstrip('/'):
                raise ValueError('Notebook workspace does not match the selected warehouse host')
            authorization = workspace.config.authenticate().get('Authorization', '')
            if not authorization.startswith('Bearer '):
                raise ValueError('Notebook did not supply bearer authentication')
            token = authorization[7:]
        elif settings['warehouse_auth'] == 'secret':
            token = dbutils.secrets.get(scope=settings['secret_scope'], key='warehouse-token')
        else:
            raise ValueError('Invalid warehouse authentication mode')
        environment = {'STEWARD_GLOBAL_DATABRICKS_ENABLED': '1',
                       'STEWARD_DATABRICKS_HOST': settings['host'],
                       'STEWARD_DATABRICKS_WAREHOUSE_ID': settings['warehouse_id'],
                       'STEWARD_DATABRICKS_TOKEN': token,
                       'STEWARD_GLOBAL_ATLAS_ENABLED': '1' if publish else '0'}
        if publish:
            environment['STEWARD_GLOBAL_ATLAS_URI'] = dbutils.secrets.get(scope=settings['secret_scope'], key='public-atlas-uri')
        previous = {name: os.environ.get(name) for name in environment}
        os.environ.update(environment)
        sys.path.insert(0, str(CODE_ROOT))
        job = importlib.import_module('integrations.global.databricks_job')
        improvement_path = CODE_ROOT / 'integrations/global/python313-improvement.json'
        improvement = json.loads(improvement_path.read_text(encoding='utf-8'))
        result = job.run([{'kind': 'osv', 'id': 'GHSA-9wx4-h78v-vm56'},
                          {'kind': 'improvement', 'record': improvement}], publish=publish)
        print(json.dumps(result))
        if result['ingestion_failed'] or result['curation']['failed'] or result['publication'].get('failed'):
            raise ValueError('Public pipeline had incomplete stages')
        return result
    except Exception:
        raise RuntimeError('Scheduled public pipeline failed; inspect safe run output, public-source access and scoped secret configuration.') from None
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


if 'dbutils' in globals():
    result = run_scheduled(dbutils)
    dbutils.notebook.exit(json.dumps(result))
