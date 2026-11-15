"""Workspace-local test directories avoid Windows sandbox private-mode ACL conflicts."""
import uuid
from pathlib import Path
import pytest


@pytest.fixture
def tmp_path():
    path = Path(__file__).resolve().parents[1] / 'data' / 'tests' / uuid.uuid4().hex
    path.mkdir(parents=True)
    return path


@pytest.fixture(autouse=True)
def _atlas_not_required(monkeypatch):
    """Tests never contact Atlas: run in the explicit non-required mode unless a test opts in."""
    monkeypatch.setenv('STEWARD_ATLAS_REQUIRED', '0')
    monkeypatch.setenv('STEWARD_ATLAS_GRAPH_MIRROR', '0')
