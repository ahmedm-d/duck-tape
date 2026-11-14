"""Single switch for "Atlas required" mode (Option B: Atlas is a compulsory part of repository knowledge).

Production (`run.py`) runs with Atlas required by default: the server refuses to start without a working
Atlas configuration, repository snapshots are synced to Atlas automatically, retrieval and catalogue
lookups use Atlas by default, and the agent graph context computes its blast radius in Atlas.

`STEWARD_ATLAS_REQUIRED=0` is the explicit escape hatch for tests, CI and the deterministic acceptance
gates, which must never contact a real cluster. In that mode the older opt-in flags apply unchanged.
"""
import os

FLAG = 'STEWARD_ATLAS_REQUIRED'


def atlas_required() -> bool:
    """True unless STEWARD_ATLAS_REQUIRED is explicitly set to 0/false/no/off."""
    return os.getenv(FLAG, '1').strip().lower() not in {'0', 'false', 'no', 'off'}
