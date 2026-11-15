import pytest
from fastapi import HTTPException
from backend import db


def test_project_environment_is_literal_and_explicit_environment_wins(tmp_path,monkeypatch):
    import run
    monkeypatch.setattr(run,'ROOT',tmp_path)
    (tmp_path/'.env').write_text('STEWARD_MODEL=from-file\nSTEWARD_SMALL_MODEL="$(must-not-execute)"\n',encoding='utf-8')
    monkeypatch.setenv('STEWARD_MODEL','from-process')
    monkeypatch.delenv('STEWARD_SMALL_MODEL',raising=False)
    run.load_environment()
    import os
    assert os.environ['STEWARD_MODEL']=='from-process'
    assert os.environ['STEWARD_SMALL_MODEL']=='$(must-not-execute)'


@pytest.mark.parametrize('body',[
    {'endpoint':'http://127.0.0.1:invalid/v1'},
    {'endpoint':'http://[invalid'},
    {'endpoint':'https://user:password@example.com/v1'},
    {'endpoint':'http://example.com/v1'},
    {'embedding_endpoint':'https://user:password@example.com/v1'},
    {'embedding_endpoint':'http://example.com/v1'},
    {'embedding_endpoint':None},
    {'key':'server-secret'},
])
def test_provider_settings_reject_invalid_endpoints_and_credentials(tmp_path,monkeypatch,body):
    from backend.main import configure_provider
    monkeypatch.setattr(db,'DATA_DIR',tmp_path)
    db.init()
    with pytest.raises(HTTPException) as error:
        configure_provider(body)
    assert error.value.status_code==422
    assert db.get('provider_settings','default') is None


def test_separate_embedding_endpoint_is_persisted(tmp_path,monkeypatch):
    from backend.main import configure_provider
    monkeypatch.setattr(db,'DATA_DIR',tmp_path)
    monkeypatch.delenv('STEWARD_EMBEDDING_ENDPOINT',raising=False)
    db.init()
    result=configure_provider({'embedding_endpoint':'http://127.0.0.1:11437/v1','embedding_model':'nomic-embed-text-v1.5'})
    assert result['configuration']['embedding_endpoint']=='http://127.0.0.1:11437/v1'


def test_lifespan_probes_sync_runners_outside_the_api_event_loop(tmp_path,monkeypatch):
    import asyncio
    from backend import main,providers,sample
    monkeypatch.setattr(db,'DATA_DIR',tmp_path)
    monkeypatch.setattr(sample,'seed_sample',lambda: {})
    monkeypatch.setattr(sample,'seed_jobs',lambda repo: None)
    monkeypatch.delenv('STEWARD_EMBEDDED_WORKER',raising=False)
    called=[]
    def probe(name):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            called.append(name)
        else:
            raise AssertionError('Synchronous provider transport cannot run inside API event loop')
    monkeypatch.setattr(providers,'start_local_runner',lambda: probe('coding'))
    monkeypatch.setattr(providers,'start_embedding_runner',lambda: probe('embedding'))
    async def start():
        async with main.lifespan(main.app):
            assert called==['coding','embedding']
    asyncio.run(start())


def test_empty_workspace_stays_empty_across_restarts(tmp_path, monkeypatch):
    import asyncio
    from backend import main, providers
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path)
    monkeypatch.setenv('STEWARD_SEED_SAMPLE', '0')
    monkeypatch.delenv('STEWARD_EMBEDDED_WORKER', raising=False)
    monkeypatch.setattr(providers, 'start_local_runner', lambda: None)
    monkeypatch.setattr(providers, 'start_embedding_runner', lambda: None)
    async def restart():
        for _ in range(2):
            async with main.lifespan(main.app):
                assert db.list_records('repositories') == []
                assert db.list_records('jobs') == []
    asyncio.run(restart())
