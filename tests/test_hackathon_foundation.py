"""Hackathon foundation contracts; no external accounts or live model calls."""
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import db, main

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("kind,endpoint", [
    ("adaption", "https://api.prod.adaptionlabs.ai/api/v1"),
    ("huggingface", "https://router.huggingface.co/v1"),
])
def test_sponsor_provider_kind_can_be_saved(tmp_path, monkeypatch, kind, endpoint):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    db.init()
    result = main.configure_provider({"kind": kind, "endpoint": endpoint, "model": "fixture-model"})
    assert result["saved"]["kind"] == kind
    assert "key" not in json.dumps(result)


def test_sponsor_env_is_literal_and_existing_values_win(tmp_path, monkeypatch):
    import run
    monkeypatch.setattr(run, "ROOT", tmp_path)
    monkeypatch.setenv("HF_TOKEN", "existing-secret")
    monkeypatch.delenv("ADAPTION_API_KEY", raising=False)
    (tmp_path / ".env").write_text('HF_TOKEN=file-secret\nADAPTION_API_KEY="$(never-run)"\n', encoding="utf-8")
    run.load_environment()
    assert os.environ["HF_TOKEN"] == "existing-secret"
    assert os.environ["ADAPTION_API_KEY"] == "$(never-run)"


@pytest.mark.parametrize("name", ["PATH", "PYTHONPATH", "HTTP_PROXY"])
def test_env_cannot_override_process_controls(tmp_path, monkeypatch, name):
    import run
    monkeypatch.setattr(run, "ROOT", tmp_path)
    (tmp_path / ".env").write_text(name + "=bad\n", encoding="utf-8")
    with pytest.raises(ValueError):
        run.load_environment()


def test_integration_status_never_probes_or_exposes_credentials(tmp_path, monkeypatch):
    from backend import providers
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    db.init()
    monkeypatch.setenv("HF_TOKEN", "private-hf-secret")
    monkeypatch.setenv("ADAPTION_API_KEY", "private-adaption-secret")
    monkeypatch.setenv("STEWARD_ATLAS_URI", "mongodb+srv://private:secret@cluster.mongodb.net/")
    monkeypatch.setenv("STEWARD_DATABRICKS_TOKEN", "private-db-token")
    monkeypatch.delenv("STEWARD_ATLAS_ENABLED", raising=False)
    monkeypatch.delenv("STEWARD_DATABRICKS_ENABLED", raising=False)
    def no_probe(*args, **kwargs):
        raise AssertionError("Status must not contact remote services")
    monkeypatch.setattr(providers, "health", no_probe)
    monkeypatch.setattr(providers, "request", no_probe)
    response = TestClient(main.app).get("/api/integrations")
    assert response.status_code == 200
    value = response.json()
    assert value["schema_version"] == 1
    assert set(value["integrations"]) == {"adaption", "huggingface", "atlas", "databricks"}
    assert value["live_verified"] is False
    assert value["integrations"]["atlas"]["enabled"] is False
    assert value["integrations"]["databricks"]["enabled"] is False
    for secret in ("private-hf-secret", "private-adaption-secret", "private-db-token", "mongodb+srv"):
        assert secret not in response.text


@pytest.mark.parametrize("subject,valid", [
    ("feat(agent): add Adaption response validation", True),
    ("fix(data)!: reject stale vector namespaces", True),
    ("docs(hackathon): document team ownership", True),
    ("fix: missing ownership scope", False),
    ("Updated stuff", False),
    ("feat(ui): " + "x" * 80, False),
    ("feat(ui): Add a panel", False),
    ("feat(ui): bad\nsecond subject", False),
])
def test_commit_subject_rules(subject, valid):
    script = ROOT / "scripts/check_commit_message.py"
    result = subprocess.run([sys.executable, str(script), "--subject", subject], capture_output=True, text=True)
    assert result.returncode == (0 if valid else 1), result.stdout + result.stderr


def test_commit_hook_accepts_body_and_checks_only_subject(tmp_path):
    message = tmp_path / "message"
    message.write_text("feat(agent): validate hosted responses\n\nKeep provider credentials separate.\n", encoding="utf-8")
    result = subprocess.run([sys.executable, str(ROOT / "scripts/check_commit_message.py"), "--message-file", str(message)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_integration_cli_dispatch_and_error_redaction(monkeypatch, capsys):
    cli = importlib.import_module("scripts.integrations")
    from backend import data_platforms
    monkeypatch.setattr(cli, "load_environment", lambda: None)
    calls = []
    monkeypatch.setattr(data_platforms, "atlas_search", lambda repo, snapshot, query, limit=12: calls.append((repo, snapshot, query, limit)) or {"matches": []})
    assert cli.main(["atlas-search", "--repo", "r", "--snapshot", "s", "--query", "quote", "--limit", "4"]) == 0
    assert calls == [("r", "s", "quote", 4)]
    assert json.loads(capsys.readouterr().out) == {"matches": []}
    def broken(*args):
        raise RuntimeError("token=DO-NOT-DISPLAY")
    monkeypatch.setattr(data_platforms, "databricks_sync", broken)
    assert cli.main(["databricks-sync", "--run", "r"]) == 1
    captured = capsys.readouterr()
    assert "DO-NOT-DISPLAY" not in captured.out + captured.err
    assert json.loads(captured.err)["status"] == "failed"


@pytest.mark.parametrize("kind,variable", [("adaption", "ADAPTION_API_KEY"), ("huggingface", "HF_TOKEN")])
def test_readiness_does_not_offer_native_credentials_to_custom_origins(tmp_path, monkeypatch, kind, variable):
    from backend import providers
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    db.init()
    monkeypatch.setenv("STEWARD_MODEL_PROVIDER", kind)
    monkeypatch.setenv("STEWARD_MODEL_ENDPOINT", "https://custom.example/v1")
    monkeypatch.setenv("STEWARD_MODEL", "fixture-model")
    monkeypatch.setenv(variable, "native-secret")
    monkeypatch.delenv("STEWARD_MODEL_KEY", raising=False)
    config = providers.configuration()
    assert config["endpoint"] == "https://custom.example/v1"
    result = main.integration_status()["integrations"][kind]
    assert result["enabled"] is True
    assert result["configured"] is False
    monkeypatch.setenv("STEWARD_MODEL_KEY", "explicit-custom-secret")
    assert main.integration_status()["integrations"][kind]["configured"] is True


def test_atlas_cli_limit_matches_adapter_boundary(monkeypatch, capsys):
    cli = importlib.import_module("scripts.integrations")
    from backend import data_platforms
    monkeypatch.setattr(cli, "load_environment", lambda: None)
    calls = []
    monkeypatch.setattr(data_platforms, "atlas_search", lambda *args, **kwargs: calls.append((args, kwargs)) or {})
    arguments = ["atlas-search", "--repo", "r", "--snapshot", "s", "--query", "q", "--limit"]
    assert cli.main(arguments + ["24"]) == 0
    with pytest.raises(SystemExit) as error:
        cli.main(arguments + ["25"])
    assert error.value.code == 2
    assert len(calls) == 1
