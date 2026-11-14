import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

import launch_researcher as lr

SECRET = "sk-test-SECRET-key-123456"
COMPLETION = json.dumps(
    {
        "findings": [
            {
                "title": "SQL injection in get_user",
                "description": "Input reaches the query unescaped.",
                "priority": "high",
                "cwe": "CWE-89",
                "reference": {"path": "app/db.py", "start_line": 3, "end_line": 4},
                "source_key": "researcher:app/db.py:get_user",
            }
        ]
    }
)
FIXED = json.dumps({"findings": []})


def write_jsonl(path: Path, completions):
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {"prompt": f"<graph_context>{i}</graph_context>\ndef f(): pass", "completion": c}
        for i, c in enumerate(completions)
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def make_run(run_id="run-1", status="running", **kw):
    base = dict(
        id=run_id, dataset_id="ds-1", model=lr.DEFAULT_MODEL, status=status, training_method="instruction",
        created_at=datetime(2026, 11, 14, tzinfo=timezone.utc), completed_at=None, iterations_completed=0.0,
        max_iterations=3.0, target_win_rate=0.8, best_win_rate=None, best_hyperparams=None,
        download_available=False, error=None,
    )
    base.update(kw)
    return NS(**base)


def raise_(exc):
    raise exc


class FakeClient:
    """Minimal stand-in for adaption.Adaption recording every call that would spend credits."""

    def __init__(self, *, remote_datasets=(), remote_runs=(), models=None):
        self.remote_datasets = list(remote_datasets)
        self.remote_runs = list(remote_runs)
        self.created = []
        self.uploads = []
        self.run = make_run()
        model_ids = models if models is not None else [lr.DEFAULT_MODEL]
        client = self

        class Datasets:
            custom_evals = NS(list=lambda ds: NS(items=[]))

            def list(self, **kw):
                return list(client.remote_datasets)

            def upload_file(self, path, **kw):
                client.uploads.append((Path(path), kw))
                return NS(dataset_id="ds-1")

            def wait_for_completion(self, dataset_id, **kw):
                return NS(status="succeeded")

            def get(self, dataset_id):
                return NS(dataset_id=dataset_id, name=lr.DATASET_NAME, status="succeeded", row_count=2, error_data=None)

        class Auto:
            def list(self, **kw):
                return [r for r in client.remote_runs if "dataset_id" not in kw or r.dataset_id == kw["dataset_id"]]

            def list_models(self):
                return NS(models=[NS(id=m, methods=["sft", "dpo"], training_types=["lora"]) for m in model_ids])

            def create(self, **kw):
                client.created.append(kw)
                client.run = make_run(model=kw["model"])
                client.remote_runs.append(client.run)
                return client.run

            def get(self, run_id):
                return client.run

            def download(self, run_id):
                return NS(write_to_file=lambda target: Path(target).write_bytes(b"weights"))

        self.datasets = Datasets()
        self.autoscientist = Auto()


@pytest.fixture
def env(tmp_path):
    train = tmp_path / "researcher.jsonl"
    evalf = tmp_path / "researcher_eval.jsonl"
    write_jsonl(train, [COMPLETION, FIXED])
    write_jsonl(evalf, [FIXED])
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"schema_version": "2.0", "runs": [{"agent": "router", "run_id": "r-router", "account_index": 1}],
                    "datasets": {}, "custom_evals": {}, "accounts": {}}),
        encoding="utf-8",
    )
    return NS(train=train, eval=evalf, manifest=manifest)


def main_args(env, *extra):
    return ["--train-file", str(env.train), "--eval-file", str(env.eval), "--manifest", str(env.manifest), *extra]


def fake_key(monkeypatch, var="ADAPTION_API_KEY_3"):
    monkeypatch.setattr(lr, "resolve_api_key", lambda *a, **k: (var, SECRET))


def researcher_entries(env):
    return [r for r in json.loads(env.manifest.read_text())["runs"] if r["agent"] == "researcher"]


def test_resolve_api_key_prefers_researcher_then_key_3():
    got = lr.resolve_api_key({"ADAPTION_API_KEY_RESEARCHER": " a ", "ADAPTION_API_KEY_3": "b"}, load_env=lambda: None)
    assert got == ("ADAPTION_API_KEY_RESEARCHER", "a")
    assert lr.resolve_api_key({"ADAPTION_API_KEY_3": "b"}, load_env=lambda: None) == ("ADAPTION_API_KEY_3", "b")
    with pytest.raises(lr.LaunchError) as exc:
        lr.resolve_api_key({}, load_env=lambda: None)
    assert "ADAPTION_API_KEY_RESEARCHER" in str(exc.value)


def test_dry_run_prints_plan_and_sends_nothing(env, monkeypatch, capsys):
    fake_key(monkeypatch)

    def boom(_key):
        raise AssertionError("dry run must not create a client")

    rc = lr.main(main_args(env, "--target-rows", "10"), client_factory=boom)
    out = capsys.readouterr()
    assert rc == 0
    for expected in ("duck_tape_researcher", "Qwen/Qwen3.6-35B-A3B", "instruction", "train rows            : 2",
                     "augmentation_domain_rows: 8", "duck-tape-researcher-v1", "nothing was sent",
                     "ADAPTION_API_KEY_3"):
        assert expected in out.out
    assert SECRET not in out.out + out.err
    assert [r["agent"] for r in json.loads(env.manifest.read_text())["runs"]] == ["router"]


def test_dry_run_without_key_still_works(env, monkeypatch, capsys):
    monkeypatch.setattr(lr, "resolve_api_key", lambda *a, **k: raise_(lr.LaunchError("none")))
    assert lr.main(main_args(env)) == 0
    assert "NOT SET" in capsys.readouterr().out


def test_dry_run_flags_invalid_rows(env, monkeypatch, capsys):
    fake_key(monkeypatch)
    write_jsonl(env.train, ["not findings json", FIXED])
    rc = lr.main(main_args(env))
    err = capsys.readouterr().err
    assert rc == 1 and "researcher.jsonl:1" in err and "FAILED" in err


def test_dry_run_missing_files_fail(tmp_path, monkeypatch, capsys):
    fake_key(monkeypatch)
    rc = lr.main(["--train-file", str(tmp_path / "no.jsonl"), "--eval-file", str(tmp_path / "no2.jsonl")])
    assert rc == 1 and "missing file" in capsys.readouterr().err


def test_augmentation_rows_never_negative():
    assert lr.compute_augmentation_rows(12000, 10000) == 0
    assert lr.compute_augmentation_rows(1500, 10000) == 8500


def test_execute_records_upload_create_and_manifest(env, monkeypatch, capsys):
    fake_key(monkeypatch)
    client = FakeClient()
    rc = lr.main(main_args(env, "--execute", "--target-rows", "10"), client_factory=lambda key: client)
    out = capsys.readouterr()
    assert rc == 0

    (path, kw), = client.uploads
    assert path == env.train
    assert kw == {"name": "duck_tape_researcher", "processing_mode": "raw",
                  "column_mapping": {"prompt": "prompt", "completion": "completion"}}
    assert client.created == [{
        "dataset_id": "ds-1", "model": "Qwen/Qwen3.6-35B-A3B", "training_method": "instruction",
        "data_format": "instruction", "augmentation_domain_rows": 8, "idempotency_key": "duck-tape-researcher-v1",
    }]

    manifest = json.loads(env.manifest.read_text())
    assert manifest["runs"][0]["agent"] == "router"  # untouched
    entry, = researcher_entries(env)
    assert entry["run_id"] == "run-1" and entry["account_index"] == 3
    assert entry["idempotency_key"] == "duck-tape-researcher-v1"
    assert (entry["base_rows"], entry["augmentation_domain_rows"], entry["effective_rows"]) == (2, 8, 10)
    assert entry["processing_mode"] == "raw" and entry["status"] == "running"
    for k in ("agent", "account_index", "account_role", "wave", "idempotency_key", "run_id", "dataset_id", "model",
              "training_method", "status", "iterations_completed", "max_iterations", "target_win_rate",
              "best_win_rate", "download_available", "error", "best_hyperparams", "base_rows",
              "augmentation_domain_rows", "effective_rows", "processing_mode", "created_at", "last_checked_at"):
        assert k in entry
    assert manifest["datasets"]["researcher"]["dataset_id"] == "ds-1"
    for blob in (out.out, out.err, env.manifest.read_text()):
        assert SECRET not in blob


def test_execute_with_researcher_key_uses_integer_account_index(env, monkeypatch):
    fake_key(monkeypatch, "ADAPTION_API_KEY_RESEARCHER")
    assert lr.main(main_args(env, "--execute"), client_factory=lambda key: FakeClient()) == 0
    entry, = researcher_entries(env)
    assert entry["account_index"] == 5 and entry["account_label"] == "researcher"


def test_execute_is_idempotent(env, monkeypatch):
    fake_key(monkeypatch)
    client = FakeClient()
    assert lr.main(main_args(env, "--execute"), client_factory=lambda k: client) == 0
    assert lr.main(main_args(env, "--execute"), client_factory=lambda k: client) == 0
    assert len(client.created) == 1 and len(client.uploads) == 1
    assert len(researcher_entries(env)) == 1


def test_execute_reuses_remote_dataset_and_adopts_existing_run(env, monkeypatch):
    fake_key(monkeypatch)
    existing = NS(dataset_id="ds-1", name="duck_tape_researcher", status="succeeded", row_count=2)
    client = FakeClient(remote_datasets=[existing], remote_runs=[make_run(run_id="run-remote")])
    assert lr.main(main_args(env, "--execute"), client_factory=lambda k: client) == 0
    assert client.uploads == [] and client.created == []
    entry, = researcher_entries(env)
    assert entry["run_id"] == "run-remote"


def test_execute_refuses_when_recorded_run_failed(env, monkeypatch, capsys):
    fake_key(monkeypatch)
    client = FakeClient()
    assert lr.main(main_args(env, "--execute"), client_factory=lambda k: client) == 0
    client.run = make_run(status="failed", error="boom")
    assert lr.main(main_args(env, "--execute"), client_factory=lambda k: client) == 1
    assert "failed" in capsys.readouterr().err and len(client.created) == 1


def test_execute_validation_error_sends_nothing(env, monkeypatch):
    fake_key(monkeypatch)
    write_jsonl(env.train, ["bad"])
    client = FakeClient()
    assert lr.main(main_args(env, "--execute"), client_factory=lambda k: client) == 1
    assert client.uploads == [] and client.created == []


def test_execute_unknown_model_fails_before_upload(env, monkeypatch, capsys):
    fake_key(monkeypatch)
    client = FakeClient(models=["other/model"])
    assert lr.main(main_args(env, "--execute"), client_factory=lambda k: client) == 1
    assert "not offered" in capsys.readouterr().err and client.uploads == []


def test_execute_respects_job_cap(env, monkeypatch, capsys):
    fake_key(monkeypatch)
    client = FakeClient(remote_runs=[make_run(run_id=f"r{i}", dataset_id=f"d{i}") for i in range(5)])
    assert lr.main(main_args(env, "--execute"), client_factory=lambda k: client) == 1
    assert "active runs" in capsys.readouterr().err and client.uploads == []


def test_custom_eval_is_opt_in_and_non_fatal(env, monkeypatch):
    fake_key(monkeypatch)
    created = []
    evals = NS(
        list=lambda ds: NS(items=[]),
        create=lambda ds, **kw: created.append((ds, kw)) or NS(custom_eval_id="ce-1", status="pending"),
    )

    client = FakeClient()
    client.datasets.custom_evals = evals
    assert lr.main(main_args(env, "--execute"), client_factory=lambda k: client) == 0
    assert created == []

    env.manifest.write_text(json.dumps({"runs": []}), encoding="utf-8")
    client2 = FakeClient()
    client2.datasets.custom_evals = evals
    assert lr.main(main_args(env, "--execute", "--custom-eval"), client_factory=lambda k: client2) == 0
    (ds, kw), = created
    assert ds == "ds-1" and kw["name"] == "researcher_findings_eval" and "empty list" in kw["judge_prompt"]
    assert json.loads(env.manifest.read_text())["custom_evals"]["researcher"]["custom_eval_id"] == "ce-1"

    # unsupported: the failure is recorded and the launch still succeeds
    env.manifest.write_text(json.dumps({"runs": []}), encoding="utf-8")
    client3 = FakeClient()
    client3.datasets.custom_evals = NS(list=lambda ds: raise_(RuntimeError("unsupported")))
    assert lr.main(main_args(env, "--execute", "--custom-eval"), client_factory=lambda k: client3) == 0
    assert json.loads(env.manifest.read_text())["custom_evals"]["researcher"]["status"] == "skipped_or_unsupported"


def test_poll_updates_manifest(env, monkeypatch, capsys):
    fake_key(monkeypatch)
    client = FakeClient()
    lr.main(main_args(env, "--execute"), client_factory=lambda k: client)
    client.run = make_run(status="succeeded", iterations_completed=3.0, best_win_rate=0.83, download_available=True,
                          completed_at=datetime(2026, 11, 15, tzinfo=timezone.utc))
    assert lr.main(main_args(env, "--poll"), client_factory=lambda k: client) == 0
    out = capsys.readouterr().out
    assert "status=succeeded" in out and "0.830" in out
    manifest = json.loads(env.manifest.read_text())
    entry, = researcher_entries(env)
    assert (entry["status"], entry["iterations_completed"], entry["best_win_rate"], entry["download_available"]) == (
        "succeeded", 3.0, 0.83, True)
    assert entry["completed_at"].startswith("2026-11-15")
    assert [r["agent"] for r in manifest["runs"]].count("router") == 1
    assert SECRET not in env.manifest.read_text()


def test_poll_without_recorded_run_errors(env, monkeypatch, capsys):
    fake_key(monkeypatch)
    assert lr.main(main_args(env, "--poll"), client_factory=lambda k: FakeClient()) == 1
    assert "--execute" in capsys.readouterr().err


def test_poll_download_saves_artifact_only_when_available(env, monkeypatch, tmp_path):
    fake_key(monkeypatch)
    client = FakeClient()
    lr.main(main_args(env, "--execute"), client_factory=lambda k: client)
    dest = tmp_path / "dl"
    lr.main(main_args(env, "--poll", "--download", str(dest)), client_factory=lambda k: client)
    assert not dest.exists() or not list(dest.iterdir())
    client.run = make_run(status="succeeded", download_available=True)
    lr.main(main_args(env, "--poll", "--download", str(dest)), client_factory=lambda k: client)
    assert (dest / "researcher_run-1.gz").read_bytes() == b"weights"


def test_execute_without_key_errors_secret_free(env, monkeypatch, capsys):
    monkeypatch.setattr(lr, "resolve_api_key", lambda *a, **k: raise_(lr.LaunchError("No Adaption API key found.")))
    assert lr.main(main_args(env, "--execute")) == 2
    assert "No Adaption API key" in capsys.readouterr().err


def test_api_errors_are_translated_and_scrubbed(env, monkeypatch, capsys):
    import adaption
    import httpx

    fake_key(monkeypatch)

    def build(exc_cls, status, message):
        resp = httpx.Response(status, request=httpx.Request("POST", "https://api.example/x"))
        return exc_cls(message, response=resp, body=None)

    cases = [
        (adaption.AuthenticationError, 401, f"bad key {SECRET}", "Authentication failed"),
        (adaption.APIStatusError, 402, f"Insufficient credits for {SECRET}", "Insufficient Adaption credits"),
        (adaption.RateLimitError, 429, "too many", "job cap"),
    ]
    for cls, status, message, expected in cases:
        client = FakeClient()
        client.autoscientist.list = lambda exc=build(cls, status, message), **kw: raise_(exc)
        env.manifest.write_text(json.dumps({"runs": []}), encoding="utf-8")
        assert lr.main(main_args(env, "--execute"), client_factory=lambda k, c=client: c) == 1
        err = capsys.readouterr()
        assert expected in err.err
        assert SECRET not in err.out + err.err
