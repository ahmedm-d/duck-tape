#!/usr/bin/env python3
"""Root wrapper delegating to model-training/scripts/evaluate_all_agents_rubric.py."""
from __future__ import annotations

from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parent.parent
MT_SCRIPTS = REPO_ROOT / "model-training" / "scripts"
MT_SRC = REPO_ROOT / "model-training" / "src"

for _p in (str(MT_SRC), str(MT_SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from evaluate_all_agents_rubric import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
