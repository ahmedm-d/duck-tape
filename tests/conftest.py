"""Workspace-local test directories avoid Windows sandbox private-mode ACL conflicts."""
import uuid
from pathlib import Path
import pytest


@pytest.fixture
def tmp_path():
    path = Path(__file__).resolve().parents[1] / 'data' / 'tests' / uuid.uuid4().hex
    path.mkdir(parents=True)
    return path
