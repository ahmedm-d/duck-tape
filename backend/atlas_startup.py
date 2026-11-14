"""Startup gate for "Atlas required" mode: refuse to serve without a working private Atlas project.

`check_atlas_ready()` is called by `run.py` only; importing `backend.main` never runs it, so tests and
tooling that import the app stay offline. Error messages are fixed text and never include the URI,
credentials or the underlying driver error.
"""
from . import atlas_mode

_ESCAPE_HATCH = ' (STEWARD_ATLAS_REQUIRED=0 is only an escape hatch for tests and CI, not for production.)'


class AtlasStartupError(RuntimeError):
    """Atlas is required but not ready; the message tells the operator what to fix."""


def use_system_trust_store() -> bool:
    """Make Python TLS use the OS trust store when `truststore` is installed.

    Needed where antivirus or a corporate proxy re-signs HTTPS with a certificate only the OS trusts.
    Verification stays on; this only changes which CA store is consulted. Returns whether it was injected.
    """
    try:
        import truststore
    except ImportError:
        return False
    truststore.inject_into_ssl()
    return True


def check_atlas_ready() -> None:
    """Raise AtlasStartupError unless Atlas is enabled, configured, importable and answers a ping.

    Does nothing when `STEWARD_ATLAS_REQUIRED=0`.
    """
    if not atlas_mode.atlas_required():
        return
    from . import data_platforms
    if not data_platforms._enabled('atlas'):
        raise AtlasStartupError('Atlas is required but not enabled. Set STEWARD_ATLAS_ENABLED=1, '
                                'STEWARD_ATLAS_URI and STEWARD_ATLAS_DIMENSIONS in .env.' + _ESCAPE_HATCH)
    try:
        config = data_platforms._atlas_config()
    except data_platforms.DataPlatformError:
        raise AtlasStartupError('Atlas is required but its configuration is incomplete or invalid. Set '
                                'STEWARD_ATLAS_URI (mongodb+srv://<user>:<password>@<cluster>.mongodb.net/) and '
                                'STEWARD_ATLAS_DIMENSIONS (the embedding dimension) in .env.' + _ESCAPE_HATCH) from None
    try:
        import pymongo  # noqa: F401
    except ImportError:
        raise AtlasStartupError('Atlas is required but pymongo is not installed. '
                                'Run: python -m pip install -r requirements.lock' + _ESCAPE_HATCH) from None
    use_system_trust_store()
    try:
        with data_platforms._atlas_collection(config) as collection:
            collection.database.command('ping')
    except Exception:
        raise AtlasStartupError('Atlas is required but the connectivity check failed. Verify the cluster is '
                                'running, this machine\'s IP is on the Atlas access list, and the database user '
                                'and password are correct; TLS certificates are verified.' + _ESCAPE_HATCH) from None
