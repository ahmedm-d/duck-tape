import json

import pytest

from backend import applicability
from backend.main import JobInput

LOCK = json.dumps({'lockfileVersion': 3, 'packages': {
    '': {'name': 'shop'},
    'node_modules/lodash': {'version': '4.17.20'},
    'node_modules/left-pad': {'version': '1.3.0'},
}}, indent=2)


def index(files, snapshot='snap-1'):
    return {'repo_id': 'repo-1', 'source_snapshot_id': snapshot, 'snapshot': 'idx-' + snapshot,
            'files': [{'path': path, 'status': 'indexed', 'content': content, 'hash': 'h:' + path + ':' + str(len(content))}
                      for path, content in files.items()]}


def entry(**changes):
    base = {'id': 'GHSA-35jh-r3h4-6jhm', 'kind': 'vulnerability', 'revision': 1, 'content_hash': 'c' * 64,
            'source_url': 'https://osv.dev/vulnerability/GHSA-35jh-r3h4-6jhm', 'summary': 'Command injection in lodash template',
            'status': 'active',
            'affected': [{'ecosystem': 'npm', 'package': 'lodash', 'ranges': [{'introduced': '0', 'fixed': '4.17.21'}]}]}
    return {**base, **changes}


def test_profile_binds_snapshot_and_cites_lockfile_lines():
    profile = applicability.build_profile(index({'package-lock.json': LOCK, 'requirements.txt': 'Flask==3.0.0\nloose>=1\n'}))
    assert profile['repo_id'] == 'repo-1' and profile['snapshot_id'] == 'snap-1'
    lodash = next(item for item in profile['packages'] if item['name'] == 'lodash')
    lines = LOCK.splitlines()
    assert 'node_modules/lodash' in lines[lodash['start_line'] - 1] and '4.17.20' in lines[lodash['end_line'] - 1]
    assert lodash['manifest_hash'] == 'h:package-lock.json:' + str(len(LOCK))
    flask = next(item for item in profile['packages'] if item['name'] == 'Flask')
    assert (flask['start_line'], flask['end_line']) == (1, 1)
    assert any('loose' in gap['detail'] and gap['ecosystem'] == 'pypi' for gap in profile['gaps'])


def test_profile_hash_is_stable_and_snapshot_specific():
    files = {'package-lock.json': LOCK}
    first, again = applicability.build_profile(index(files)), applicability.build_profile(index(files))
    assert first['profile_hash'] == again['profile_hash']
    assert applicability.build_profile(index(files, 'snap-2'))['profile_hash'] != first['profile_hash']
    with pytest.raises(ValueError):
        applicability.build_profile({'files': []})


def test_exact_affected_version_matches_with_citation():
    found = applicability.match(applicability.build_profile(index({'package-lock.json': LOCK})), entry())
    assert found['status'] == 'matched'
    assert [(item['name'], item['version']) for item in found['evidence']] == [('lodash', '4.17.20')]
    assert found['citations'][0]['path'] == 'package-lock.json' and found['citations'][0]['start_line']
    assert (found['entry_id'], found['entry_revision'], found['entry_hash']) == ('GHSA-35jh-r3h4-6jhm', 1, 'c' * 64)
    assert applicability.VERSION_LIMIT in found['limitations']
    assert any('4.17.21' in item for item in found['acceptance_criteria'])


def test_fixed_version_and_unrelated_repository_do_not_match():
    fixed = LOCK.replace('4.17.20', '4.17.21')
    assert applicability.match(applicability.build_profile(index({'package-lock.json': fixed})), entry())['status'] == 'not_matched'
    unrelated = applicability.build_profile(index({'requirements.txt': 'Flask==3.0.0\n'}))
    found = applicability.match(unrelated, entry())
    assert found['status'] == 'not_matched' and not found['evidence'] and not found['citations']


def test_unresolved_manifest_and_unsupported_ecosystem_are_unknown():
    yarn_only = applicability.build_profile(index({'package.json': '{"dependencies":{"lodash":"^4.17.0"}}', 'yarn.lock': 'lodash@^4.17.0:\n  version "4.17.20"\n'}))
    found = applicability.match(yarn_only, entry())
    assert found['status'] == 'unknown' and any('presence unknown' in item for item in found['limitations'])
    cargo = entry(affected=[{'ecosystem': 'crates.io', 'package': 'serde', 'versions': ['1.0.0']}])
    assert applicability.match(applicability.build_profile(index({'package-lock.json': LOCK})), cargo)['status'] == 'unknown'


def test_python_gap_does_not_block_unrelated_npm_decision():
    profile = applicability.build_profile(index({'package-lock.json': LOCK.replace('4.17.20', '4.17.21'), 'requirements.txt': 'loose>=1\n'}))
    assert applicability.match(profile, entry())['status'] == 'not_matched'


def test_explicit_versions_non_numeric_ranges_and_pypi_name_normalisation():
    profile = applicability.build_profile(index({'requirements.txt': 'Zope_Interface==5.4.0\n'}))
    listed = entry(affected=[{'ecosystem': 'PyPI', 'package': 'zope.interface', 'versions': ['5.4.0']}])
    assert applicability.match(profile, listed)['status'] == 'matched'
    git_range = entry(affected=[{'ecosystem': 'PyPI', 'package': 'zope-interface', 'ranges': [{'introduced': 'a1b2c3'}]}])
    assert applicability.match(profile, git_range)['status'] == 'unknown'


def test_match_key_ignores_rescans_but_changes_with_material_revision():
    profile = applicability.build_profile(index({'package-lock.json': LOCK}))
    first = applicability.match(profile, entry(retrieved_at='2026-11-14T10:00:00Z'))
    rescan = applicability.match(profile, entry(retrieved_at='2026-11-14T16:00:00Z'))
    revised = applicability.match(profile, entry(revision=2, content_hash='d' * 64))
    assert first['match_key'] == rescan['match_key'] != revised['match_key']


def test_withdrawn_entry_is_labelled_and_withheld_from_intake():
    profile = applicability.build_profile(index({'package-lock.json': LOCK}))
    withdrawn = entry(status='withdrawn')
    found = applicability.match(profile, withdrawn)
    assert found['entry_status'] == 'withdrawn' and any('withdrawn' in item for item in found['limitations'])
    with pytest.raises(ValueError, match='withdrawn'):
        applicability.intake_payloads(found, withdrawn)


def test_intake_payload_uses_existing_sources_and_binds_revision():
    profile = applicability.build_profile(index({'package-lock.json': LOCK}))
    found = applicability.match(profile, entry())
    payload = applicability.intake_payloads(found, entry())[0]
    job = JobInput(**payload)
    assert (job.source, job.type) == ('security', 'security')
    assert job.source_key == 'GHSA-35jh-r3h4-6jhm:lodash:4.17.20'
    assert 'catalogue revision 1' in job.description and 'not exploitability' in job.description
    assert applicability.intake_payloads(applicability.match(profile, entry(retrieved_at='later')), entry(retrieved_at='later'))[0] == payload
    assert applicability.intake_payloads(applicability.match(profile, entry(revision=2)), entry(revision=2))[0]['description'] != payload['description']
    with pytest.raises(ValueError, match='different catalogue revision'):
        applicability.intake_payloads(found, entry(revision=2))
    improvement = entry(kind='improvement')
    assert JobInput(**applicability.intake_payloads(applicability.match(profile, improvement), improvement)[0]).source == 'analysis'


def test_non_matches_never_become_proposals_and_bad_entries_are_rejected():
    profile = applicability.build_profile(index({'requirements.txt': 'Flask==3.0.0\n'}))
    with pytest.raises(ValueError, match='not_matched'):
        applicability.intake_payloads(applicability.match(profile, entry()), entry())
    for bad in [entry(kind='rumour'), entry(revision=0), entry(content_hash=''), entry(affected=[{'ecosystem': 'npm'}])]:
        with pytest.raises(ValueError):
            applicability.match(profile, bad)
