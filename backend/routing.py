"""Persist browser addresses independently of repository names and snapshots."""
import json
import re
import sqlite3
from urllib.parse import urlsplit

from . import db

RESERVED = {'api', 'assets', 'dashboard', 'settings', 'screens'}


def slug(value: object) -> str:
    return re.sub(r'[^a-z0-9_.-]+', '-', str(value).lower()).strip('.-')[:80] or 'repository'


def assign_routes(con: sqlite3.Connection) -> None:
    repos = [json.loads(row[0]) for row in con.execute("SELECT data FROM records WHERE kind='repositories' ORDER BY rowid")]
    used = {(r['route_owner'], r['route_name']) for r in repos if r.get('route_owner') and r.get('route_name')}
    for repo in repos:
        if repo.get('route_owner') and repo.get('route_name'):
            continue
        owner, name = 'local', slug(repo.get('name', 'repository'))
        origin = repo.get('origin') or {}
        source = origin.get('source', '') if isinstance(origin, dict) else str(origin)
        parsed = urlsplit(source)
        parts = parsed.path.strip('/').split('/')
        if parsed.scheme == 'https' and len(parts) >= 2:
            owner, name = slug(parts[-2]), slug(parts[-1].removesuffix('.git'))
        if repo.get('is_sample'):
            owner, name = 'demo', 'parcel'
        if owner in RESERVED:
            owner = 'repo-' + owner
        candidate, suffix = name, 2
        while (owner, candidate) in used:
            candidate = f'{name}-{suffix}'
            suffix += 1
        used.add((owner, candidate))
        repo.update(route_owner=owner, route_name=candidate)
        db.put_in(con, 'repositories', repo)


def ensure_routes() -> None:
    db.atomic(assign_routes)
