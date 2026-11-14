"""Public evidence only: bounded records and an isolated revisioned catalogue."""
from __future__ import annotations

import hashlib
import ipaddress
import json
from pathlib import Path
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class PublicRecord(BaseModel):
    model_config = ConfigDict(extra='forbid', validate_assignment=True)


def public_url(value: str) -> str:
    """Validate persisted attribution without DNS; fetching reuses pinned public DNS."""
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.fragment or parsed.port not in (None, 443) or parsed.query
            or '.' not in parsed.hostname or parsed.hostname.endswith(('.local', '.localhost', '.internal'))):
        raise ValueError('Public attribution requires credential-free HTTPS URLs')
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        return value
    if not address.is_global:
        raise ValueError('Private public-source address')
    return value


def timestamp(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Timezone-aware timestamp required')
    return parsed.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def package_identity(ecosystem: str, package: str) -> tuple[str, str]:
    ecosystem = {'pypi': 'PyPI', 'npm': 'npm'}.get(ecosystem.lower(), ecosystem)
    if ecosystem == 'PyPI':
        package = re.sub(r'[-_.]+', '-', package).lower()
    return ecosystem, package


class VersionEvent(PublicRecord):
    introduced: str | None = Field(default=None, max_length=256)
    fixed: str | None = Field(default=None, max_length=256)
    last_affected: str | None = Field(default=None, max_length=256)
    limit: str | None = Field(default=None, max_length=256)

    @model_validator(mode='after')
    def one_event(self) -> VersionEvent:
        values = [value for value in self.model_dump().values() if value is not None]
        if len(values) != 1 or not values[0].strip():
            raise ValueError('Version events require exactly one nonempty boundary')
        return self


class VersionRange(PublicRecord):
    type: str = Field(max_length=64)
    events: list[VersionEvent] = Field(default_factory=list, max_length=100)


class AffectedPackage(PublicRecord):
    ecosystem: str = Field(min_length=1, max_length=64)
    package: str = Field(min_length=1, max_length=256)
    ranges: list[VersionRange] = Field(default_factory=list, max_length=100)
    versions: list[str] = Field(default_factory=list, max_length=2000)
    fixed_versions: list[str] = Field(default_factory=list, max_length=100)

    @field_validator('versions', 'fixed_versions')
    @classmethod
    def bounded_versions(cls, values: list[str]) -> list[str]:
        if any(not value.strip() or len(value) > 256 for value in values):
            raise ValueError('Version strings require 1–256 characters')
        return sorted(set(values))


class GlobalEntry(PublicRecord):
    canonical_id: str = Field(min_length=1, max_length=200, pattern=r'^[A-Za-z0-9][A-Za-z0-9:._/-]*$')
    aliases: list[str] = Field(default_factory=list, max_length=100)
    kind: Literal['vulnerability', 'improvement', 'framework_update', 'research_update']
    title: str = Field(min_length=1, max_length=1000)
    summary: str = Field(min_length=1, max_length=40000)
    source_url: str = Field(max_length=2048)
    published_at: str
    retrieved_at: str
    source_content_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    material_revision: int = Field(default=1, ge=1, strict=True)
    affected: list[AffectedPackage] = Field(default_factory=list, max_length=200)
    conditions: list[str] = Field(default_factory=list, max_length=100)
    evidence: list[str] = Field(min_length=1, max_length=100)
    limitations: list[str] = Field(default_factory=list, max_length=100)
    withdrawn_at: str | None = None
    source_modified_at: str | None = None
    source_quality: Literal['unreviewed', 'reviewed', 'fixture'] = 'unreviewed'

    _url = field_validator('source_url')(public_url)
    _time = field_validator('published_at', 'retrieved_at')(timestamp)

    @field_validator('withdrawn_at', 'source_modified_at')
    @classmethod
    def withdrawal_time(cls, value: str | None) -> str | None:
        return timestamp(value) if value else None

    @field_validator('aliases')
    @classmethod
    def alias_identifiers(cls, values: list[str]) -> list[str]:
        if any(not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9:._/-]{0,199}', value) for value in values):
            raise ValueError('Invalid public source alias')
        return values

    @model_validator(mode='after')
    def bounded_record(self) -> GlobalEntry:
        if len(self.model_dump_json().encode()) > 256 * 1024:
            raise ValueError('Public entry exceeds 256 KiB')
        return self

    @field_validator('aliases', 'conditions', 'evidence', 'limitations')
    @classmethod
    def bounded_text(cls, values: list[str]) -> list[str]:
        if any(not item.strip() or len(item) > 4096 for item in values):
            raise ValueError('Public text items must contain 1–4096 characters')
        return sorted(set(values))

    @field_validator('affected')
    @classmethod
    def normalize_packages(cls, values: list[AffectedPackage]) -> list[AffectedPackage]:
        for item in values:
            item.ecosystem, item.package = package_identity(item.ecosystem, item.package)
        return sorted(values, key=lambda item: (item.ecosystem, item.package))


def content_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def material_hash(entry: GlobalEntry) -> str:
    return content_hash(entry.model_dump(exclude={'retrieved_at', 'source_modified_at', 'material_revision', 'source_content_hash'}))


def normalize_osv(record: dict, *, retrieved_at: str) -> GlobalEntry:
    identifier = record['id']
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', identifier):
        raise ValueError('Invalid OSV identifier')
    affected = []
    for item in record.get('affected', []):
        ranges = [{'type': rule['type'], 'events': rule.get('events', [])} for rule in item.get('ranges', [])]
        affected.append({'ecosystem': item['package']['ecosystem'], 'package': item['package']['name'],
                         'ranges': ranges, 'versions': item.get('versions', []),
                         'fixed_versions': sorted({event['fixed'] for rule in ranges for event in rule['events'] if event.get('fixed')})})
    url = f'https://osv.dev/vulnerability/{identifier}'
    aliases = sorted(set([identifier] + record.get('aliases', [])))
    canonical = min(aliases, key=lambda alias: (not alias.startswith('CVE-'), alias))
    return GlobalEntry(canonical_id=canonical, aliases=aliases,
                       kind='vulnerability', title=record.get('summary') or identifier,
                       summary=record.get('details') or record.get('summary') or identifier,
                       source_url=url, published_at=record['published'], retrieved_at=retrieved_at,
                       source_content_hash=content_hash(record), affected=affected, evidence=[url],
                       withdrawn_at=record.get('withdrawn'), source_modified_at=record.get('modified'),
                       limitations=['Version applicability does not establish exploitability.'])


def normalize_improvement(record: dict, *, retrieved_at: str) -> GlobalEntry:
    payload = dict(record)
    payload.setdefault('kind', 'improvement')
    if payload['kind'] == 'vulnerability':
        raise ValueError('Vulnerabilities require the OSV normalizer')
    payload.update(retrieved_at=retrieved_at, source_content_hash=content_hash(record))
    return GlobalEntry.model_validate(payload)


class Catalogue:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript('''
                CREATE TABLE IF NOT EXISTS global_entries (id TEXT PRIMARY KEY, payload TEXT NOT NULL, hash TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS global_aliases (alias TEXT PRIMARY KEY, id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS global_revisions (id TEXT NOT NULL, revision INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(id,revision));
                CREATE TABLE IF NOT EXISTS global_packages (id TEXT NOT NULL, ecosystem TEXT NOT NULL, package TEXT NOT NULL, PRIMARY KEY(id,ecosystem,package));
                CREATE INDEX IF NOT EXISTS global_package_lookup ON global_packages(ecosystem,package);
            ''')

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=15)

    def record_operation(self, operation: str, identity: str, state: str, detail: str) -> None:
        with self._connect() as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS global_operations '
                               '(operation TEXT, identity TEXT, state TEXT, detail TEXT, attempted_at TEXT, '
                               'PRIMARY KEY(operation,identity))')
            connection.execute('INSERT OR REPLACE INTO global_operations VALUES (?,?,?,?,?)',
                               (operation, identity, state, detail, datetime.now(timezone.utc).isoformat()))

    def operation_status(self) -> list[dict]:
        with self._connect() as connection:
            if not connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='global_operations'").fetchone():
                return []
            connection.row_factory = sqlite3.Row
            return [dict(row) for row in connection.execute(
                'SELECT * FROM global_operations ORDER BY attempted_at DESC LIMIT 100')]

    def ingest(self, entry: GlobalEntry) -> GlobalEntry:
        entry = GlobalEntry.model_validate(entry.model_dump())
        aliases = sorted(set(entry.aliases + [entry.canonical_id]))
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            owners = {row[0] for alias in aliases for row in connection.execute('SELECT id FROM global_aliases WHERE alias=?', (alias,))}
            if len(owners) > 1:
                raise ValueError('Alias collision requires explicit source reconciliation')
            canonical_id = next(iter(owners), entry.canonical_id)
            existing = connection.execute('SELECT payload,hash FROM global_entries WHERE id=?', (canonical_id,)).fetchone()
            previous = GlobalEntry.model_validate_json(existing[0]) if existing else None
            if previous:
                if previous.kind != entry.kind:
                    raise ValueError('Aliases cannot merge different entry kinds')
                if previous.source_url != entry.source_url and not (
                        previous.source_url.startswith('https://osv.dev/vulnerability/')
                        and entry.source_url.startswith('https://osv.dev/vulnerability/')
                        and previous.kind == 'vulnerability'):
                    raise ValueError('Aliases cannot replace primary source attribution')
                aliases = sorted(set(aliases + previous.aliases))
                # Alias spelling changes do not replace the original primary attribution.
                source_url = entry.source_url
                entry.source_url = previous.source_url
                entry.evidence = sorted(set([previous.source_url if value == source_url else value for value in entry.evidence]))
            entry.canonical_id = canonical_id
            entry.aliases = aliases
            digest = material_hash(entry)
            entry.material_revision = previous.material_revision + (digest != existing[1]) if previous else 1
            if previous and datetime.fromisoformat(entry.retrieved_at) < datetime.fromisoformat(previous.retrieved_at):
                raise ValueError('Out-of-order source retrieval cannot replace current evidence')
            if previous and previous.source_modified_at and (not entry.source_modified_at or datetime.fromisoformat(entry.source_modified_at) < datetime.fromisoformat(previous.source_modified_at)):
                raise ValueError('Older source revision cannot replace current evidence')
            payload = entry.model_dump_json()
            connection.execute('INSERT OR REPLACE INTO global_entries VALUES (?,?,?)', (canonical_id, payload, digest))
            connection.execute('INSERT OR IGNORE INTO global_revisions VALUES (?,?,?)', (canonical_id, entry.material_revision, payload))
            for alias in aliases:
                connection.execute('INSERT OR IGNORE INTO global_aliases VALUES (?,?)', (alias, canonical_id))
            connection.execute('DELETE FROM global_packages WHERE id=?', (canonical_id,))
            connection.executemany('INSERT OR IGNORE INTO global_packages VALUES (?,?,?)', [(canonical_id, item.ecosystem, item.package) for item in entry.affected])
        return entry

    def entries(self, limit: int = 100) -> list[GlobalEntry]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError('Entry limit must be between 1 and 1000')
        with self._connect() as connection:
            rows = connection.execute('SELECT payload FROM global_entries ORDER BY id LIMIT ?', (limit + 1,)).fetchall()
            if len(rows) > limit:
                raise ValueError('Catalogue exceeds bounded export limit; narrow the publication batch')
            return [GlobalEntry.model_validate_json(row[0]) for row in rows]

    def query(self, ecosystem: str, package: str, limit: int = 20) -> dict:
        validate_query(ecosystem, package, limit)
        ecosystem, package = package_identity(ecosystem, package)
        with self._connect() as connection:
            rows = connection.execute('SELECT payload FROM global_entries JOIN global_packages USING(id) WHERE ecosystem=? AND package=? ORDER BY id LIMIT ?', (ecosystem, package, limit + 1)).fetchall()
        result = query_result([GlobalEntry.model_validate_json(row[0]) for row in rows], ecosystem, package, limit)
        result['source_status'] = self.operation_status()
        if result['source_status']:
            with self._connect() as connection:
                for entry in result['entries']:
                    sources = {entry['source_url']} | {f'https://api.osv.dev/v1/vulns/{alias}' for alias in entry['aliases']}
                    placeholders = ','.join('?' for _ in sources)
                    latest = connection.execute(
                        f"SELECT state FROM global_operations WHERE operation='ingest' AND identity IN ({placeholders}) ORDER BY attempted_at DESC LIMIT 1",
                        tuple(sources)).fetchone()
                    if latest and latest[0] == 'failed':
                        entry.update(freshness='unknown', source_status='failed')
        return result


def validate_query(ecosystem: str, package: str, limit: int) -> None:
    if (type(limit) is not int or not 1 <= limit <= 100 or not isinstance(ecosystem, str)
            or not isinstance(package, str) or not ecosystem.strip() or not package.strip()
            or len(ecosystem) > 64 or len(package) > 256):
        raise ValueError('Exact ecosystem/package and limit between 1 and 100 required')


def query_result(records: list[GlobalEntry], ecosystem: str, package: str, limit: int = 20) -> dict:
    validate_query(ecosystem, package, limit)
    ecosystem, package = package_identity(ecosystem, package)
    entries = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    for record in records:
        entry = GlobalEntry.model_validate(record.model_dump())
        rules = [item for item in entry.affected if (item.ecosystem, item.package) == (ecosystem, package)]
        if not rules:
            continue
        entries.append({**entry.model_dump(mode='json'),
                        'freshness': 'stale' if datetime.fromisoformat(entry.retrieved_at) < cutoff else 'fresh',
                        'version_status': 'unsupported' if any(rule.ranges for rule in rules) else 'exact_versions' if all(rule.versions for rule in rules) else 'unknown'})
        if len(entries) > limit:
            break
    return {'ecosystem': ecosystem, 'package': package, 'entries': entries[:limit], 'coverage': 'unknown',
            'truncated': len(entries) > limit, 'limitations': ['Catalogue coverage is partial; no results does not mean unaffected.', 'Range evaluation belongs to the local applicability matcher.']}
