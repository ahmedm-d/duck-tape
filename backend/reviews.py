"""Read-only model reviews, persisted by the existing leased operations queue."""
import base64
import hashlib
import json
import re
from urllib.parse import quote, urlparse

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import agent_context, db, network, operations, providers, repository, retrieval, workflow
from .contracts import ProviderRequest

router = APIRouter()
NAME = re.compile(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+')
SHA = re.compile(r'[a-f0-9]{40}')


def github_name(value):
    parsed = urlparse(value)
    name = parsed.path.strip('/').removesuffix('.git')
    if (parsed.scheme != 'https' or parsed.netloc != 'github.com' or parsed.query or parsed.fragment
            or not NAME.fullmatch(name) or any(p in ('.', '..') for p in name.split('/'))):
        raise ValueError('Connect a public HTTPS GitHub repository before reviewing its PRs')
    return name


def fetch_json(path, cancel):
    cancel.raise_if_cancelled()
    data = network.fetch_public({'url': 'https://api.github.com/repos/' + path}, {'max_bytes': 2_000_000})
    cancel.raise_if_cancelled()
    return json.loads(data['data'])


def safe_text(value):
    return isinstance(value, str) and not repository.SECRET.search(value) and not repository.SECRET_ASSIGNMENT.search(value)


def pull_context(name, number, expected_head, cancel):
    """Never trust response URLs; fetch bounded source at the immutable head SHA."""
    path = f'{name}/pulls/{number}'
    pr = fetch_json(path, cancel)
    head, base = pr['head']['sha'], pr['base']['sha']
    head_repo = pr['head']['repo']['full_name']
    if (not SHA.fullmatch(head) or not SHA.fullmatch(base) or head != expected_head
            or not NAME.fullmatch(head_repo) or pr['base']['repo']['full_name'].lower() != name.lower()):
        raise ValueError('PR identity or head changed; refresh the PR list and review again')
    if pr.get('changed_files', 0) > 50:
        raise ValueError('PR exceeds the 50-file review limit; split it before review')
    rows = fetch_json(path + '/files?per_page=100', cancel)
    if not isinstance(rows, list) or len(rows) != pr.get('changed_files') or len(rows) > 50:
        raise ValueError('Incomplete PR file inventory')
    items, patches, missing = [], [], ['PR review is static; no checks were executed by this review.']
    size = 0
    for row in rows:
        cancel.raise_if_cancelled()
        file = row['filename']
        if repository.unsafe_path(file) or repository.secret_path(file) or any(p.casefold() in repository.EXCLUDED_DIRS for p in file.split('/')):
            missing.append('Excluded secret, generated or unsafe file')
            continue
        if row.get('status') == 'removed':
            missing.append(file + ': deleted file; head source unavailable')
            continue
        if size >= 100_000:
            missing.append(file + ': context budget exhausted')
            continue
        blob = fetch_json(f'{head_repo}/contents/{quote(file, safe="/")}?ref={head}', cancel)
        if blob.get('type') != 'file' or blob.get('encoding') != 'base64' or blob.get('size', 200001) > 100_000:
            missing.append(file + ': unsupported, linked or oversized source')
            continue
        try:
            content = base64.b64decode(blob['content']).decode('utf-8')
        except (ValueError, UnicodeError):
            missing.append(file + ': non-text source')
            continue
        patch = row.get('patch')
        if not safe_text(content) or (patch is not None and not safe_text(patch)) or '\x00' in content:
            missing.append(file + ': secret-bearing or binary source excluded')
            continue
        cost = len(content.encode()) + len((patch or '').encode())
        if size + cost > 120_000:
            missing.append(file + ': context budget exhausted')
            continue
        size += cost
        items.append({'path': file, 'content': content, 'start_line': 1, 'end_line': max(1,len(content.splitlines())),
                      'blob_hash': hashlib.sha256(content.encode()).hexdigest(),
                      'source_url': f'https://github.com/{head_repo}/blob/{head}/{quote(file, safe="/")}'})
        if patch: patches.append({'path': file, 'patch': patch})
        else: missing.append(file + ': GitHub omitted patch hunks')
    if not items:
        raise ValueError('No eligible PR source is available for a cited review')
    return {'items': items, 'patches': patches, 'missing': missing,
            'pr': {'repository': name, 'number': number, 'head': head, 'base': base,
                   'url': f'https://github.com/{name}/pull/{number}'}}


def fit_context(body, task, role=None, reserve=0):
    """Keep source line boundaries and report omissions before inference."""
    from .agent_adapters import prompt_messages
    from .model_schemas import schema_for
    target = providers.resolve_target(task, role)
    capability = providers.capabilities(task)
    limit = target.get('context_limit', capability['context_limit']) if target['specialist'] else capability['context_limit']
    while True:
        messages = prompt_messages(role, body, system=None if role else providers._system(task),
                                   schema=None if role else schema_for(task, payload=body))
        size = sum(len(message['content'].encode()) + 16 for message in messages)
        if size + min(4096, capability['max_output_tokens']) + reserve <= limit:
            return
        context = body['context']
        if body.get('specialist_review'):
            body.pop('specialist_review')
        elif body.get('graph_context'):
            body['graph_context'] = None
        elif context.get('patches'):
            context['patches'].pop()
        elif body.get('approved_knowledge'):
            body['approved_knowledge'].pop()
        elif len(context['items']) > 1:
            context['items'].pop()
        elif len(context['items'][0]['content'].splitlines()) > 1:
            item = context['items'][0]
            lines = item['content'].splitlines()
            lines = lines[:max(1, len(lines)//2)]
            item['content'] = '\n'.join(lines)
            item['end_line'] = item.get('start_line', 1) + len(lines) - 1
        else:
            raise ValueError('No complete source evidence fits the configured model context; increase STEWARD_CONTEXT_LIMIT or review smaller files')
        notice = 'Some evidence or specialist commentary was omitted to fit the configured model context.'
        if notice not in context['missing']:
            context['missing'].append(notice)


def execute(repo_id, payload, cancel):
    repo = db.get('repositories', repo_id)
    snapshot = payload['snapshot_id']
    if not repo or (repo.get('source_snapshot_id') or repo['snapshot']) != snapshot:
        raise ValueError('Repository snapshot changed; start a new review')
    query = 'security authentication authorization injection deserialization subprocess path traversal TLS secrets'
    if payload['kind'] == 'pr':
        context = pull_context(github_name(repo.get('origin', {}).get('source', '')), payload['number'], payload['head'], cancel)
        query = ' '.join(item['path'] for item in context['items'])[:4000]
    else:
        context = {'items': [], 'missing': ['Bounded security review, not an exhaustive audit or proof of exploitability.']}
    local = retrieval.retrieve(repo_id, snapshot, query, budget_tokens=10000, limit=24, cancel=cancel)
    if payload['kind'] == 'security': context['items'] = local['items']
    if not context['items']: raise ValueError('No indexed evidence available for review')
    context['missing'].extend(local.get('missing', []))
    # Private notes, job comments and draft policies are never read by this operation.
    body = {'review_kind': payload['kind'], 'context': context, 'approved_knowledge': local.get('knowledge', []),
            'graph_context': agent_context.agent_graph_context(repo_id, snapshot, query, cancel=cancel) if payload['kind']=='security' else None}
    if payload['kind']=='pr': context['missing'].append('Only changed head files were inspected; local-snapshot source and graph are excluded to avoid mixing revisions.')
    role = 'security_reviewer_dpo' if payload['kind'] == 'security' else 'reviewer_dpo'
    telemetry = []
    fit_context(body, 'audit')
    if providers.resolve_target('document', role)['specialist']:
        fit_context(body, 'document', role)
        result = providers.complete(ProviderRequest(task='document', payload=body, repo_id=repo_id, snapshot_id=snapshot,
                                   agent_role=role, cache=False, max_output_tokens=4096), cancel=cancel)
        body['specialist_review'] = result.output
        telemetry.append(result.telemetry)
    fit_context(body, 'audit')
    result = providers.complete(ProviderRequest(task='audit', payload=body, repo_id=repo_id, snapshot_id=snapshot,
                               risk='high', cache=False, max_output_tokens=4096), cancel=cancel)
    telemetry.append(result.telemetry)
    findings = []
    for finding in result.output['findings']:
        ref = finding['reference']
        source = next((item for item in context['items'] if item['path'] == ref['path'] and
                       item['start_line'] <= ref['start_line'] <= ref['end_line'] <= item['end_line']), None)
        if not source: raise ValueError('Review citation does not resolve to supplied source evidence')
        findings.append({**finding, 'status': 'hypothesis', 'evidence': source})
    cancel.raise_if_cancelled()
    current = db.get('repositories', repo_id)
    if not current or (current.get('source_snapshot_id') or current['snapshot']) != snapshot:
        raise ValueError('Repository changed during review; results were not published')
    if payload['kind'] == 'pr':
        latest = fetch_json(f"{context['pr']['repository']}/pulls/{payload['number']}", cancel)
        if latest['head']['sha'] != context['pr']['head'] or latest['base']['sha'] != context['pr']['base']:
            raise ValueError('PR changed during review; results were not published')
    return {'kind': payload['kind'], 'repo_id': repo_id, 'snapshot_id': snapshot, 'created_at': db.now(),
            'summary': result.output['summary'], 'findings': findings, 'provider': telemetry,
            'limitations': [*context['missing'], *result.output['limitations']], 'pr': context.get('pr'),
            'context': body['context'], 'approved_knowledge': body['approved_knowledge'], 'graph_context': body['graph_context'], 'checks_executed': False, 'approval': 'No GitHub approval or merge performed'}


class ReviewInput(BaseModel):
    number: int = Field(ge=1, le=100000000)
    head: str = Field(pattern=r'^[a-f0-9]{40}$')


def submit(repo_id, kind, number=None, head=None):
    repo = db.get('repositories', repo_id)
    if not repo: raise HTTPException(404, 'Repository not found')
    snapshot = repo.get('source_snapshot_id') or repo['snapshot']
    if kind == 'pr':
        try: github_name(repo.get('origin', {}).get('source', ''))
        except ValueError as exc: raise HTTPException(422, str(exc)) from None
    payload = {'kind': kind, 'snapshot_id': snapshot, 'number': number, 'head': head}
    # The queue coalesces identical pending work; a completed review can be run again after the PR base moves.
    return operations.submit('audit', repo_id, payload)


@router.post('/api/repositories/{repo_id}/security-review')
def security_review(repo_id: str):
    return submit(repo_id, 'security')


@router.post('/api/repositories/{repo_id}/pr-review')
def pr_review(repo_id: str, body: ReviewInput):
    return submit(repo_id, 'pr', body.number, body.head)


@router.get('/api/repositories/{repo_id}/model-reviews')
def list_reviews(repo_id: str):
    if not db.get('repositories', repo_id): raise HTTPException(404, 'Repository not found')
    return [item for item in workflow.list_work() if item['kind'] == 'audit' and item['subject'] == repo_id]
