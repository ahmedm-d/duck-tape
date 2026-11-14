"""Explicitly labelled, reviewed fixture repository and deterministic scenarios."""
import hashlib
import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

FILES = {
    'README.md': '# Parcel sample (fixture)\n\nEndpoint app.quote calls pricing.total and config.TAX_RATE.\nConstraint: use integer cents, never floating-point money. Empty orders return zero.\nThe dependency advisory is illustrative fixture data, never a live OSV claim.\n',
    'app.py': 'from pricing import total\nfrom config import TAX_RATE\n\ndef quote(items):\n    return {"total_cents": total(items, TAX_RATE)}\n',
    'config.py': 'TAX_RATE = 10\nMAX_ITEMS = 100\n',
    'pricing.py': 'def total(items, tax_rate):\n    subtotal = sum(item["price_cents"] for item in items)\n    return subtotal + subtotal * tax_rate // 100\n',
    'search.py': 'def unique_labels(labels):\n    result = []\n    for label in labels:\n        if label not in result:\n            result.append(label)\n    return result\n',
    'requirements.lock': '# Fixture only; not installed\nfixture-parser==1.0.0\n',
    'tests/test_quote.py': 'import unittest\nfrom app import quote\n\nclass QuoteTest(unittest.TestCase):\n    def test_single(self):\n        self.assertEqual(quote([{ "price_cents": 100, "quantity": 1}])["total_cents"], 110)\n    def test_empty(self):\n        self.assertEqual(quote([])["total_cents"], 0)\n',
    'tests/test_search.py': 'import unittest\nfrom search import unique_labels\n\nclass SearchTest(unittest.TestCase):\n    def test_order(self):\n        self.assertEqual(unique_labels(["b", "a", "b"]), ["b", "a"])\n',
}

SCENARIOS = [
    ('bug', 'Quantity is ignored when calculating a quote', 'ticket', 'fix', 'A quote for two items at 100 cents should be 220 cents including tax. Trace app.py, pricing.py and config.py.'),
    ('feature', 'Choose how oversized orders are handled', 'request', 'feature', 'Enforce config.MAX_ITEMS at the quote endpoint. Decide whether to reject or truncate orders.'),
    ('security', 'Fixture advisory: update fixture-parser', 'security', 'security', 'Fixture advisory STEWARD-DEMO-001 affects fixture-parser 1.0.0. Update the lock to 1.0.1. This is not a live vulnerability or exploitability claim.'),
    ('performance', 'Measure stable label deduplication improvement', 'research', 'performance', 'Compare list membership with ordered dictionary deduplication over 5000 labels; preserve first-seen order. Source: bundled search.py fixture.'),
]

def git(root, *args):
    env={k:v for k,v in os.environ.items() if k.upper() in ('SYSTEMROOT','WINDIR','PATH','TEMP','TMP')}
    env.update(GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL=os.devnull,GIT_TERMINAL_PROMPT='0')
    executable=shutil.which('git')
    if not executable:
        raise ValueError('Git executable unavailable')
    return subprocess.run([executable, '-c', 'core.longpaths=true', '-c', 'core.hooksPath='+os.devnull, '-c', 'core.attributesFile='+os.devnull, '-c', 'credential.helper=', '-c', 'core.autocrlf=false', '-c', 'core.fsmonitor=false', *args], cwd=root, capture_output=True, text=True, check=True, timeout=20,env=env).stdout

def seed_sample(force_new=False):
    from . import db, repository, snapshots
    existing = [r for r in db.list_records('repositories') if r.get('is_sample')]
    if existing and not force_new:
        seed_jobs(existing[0])
        seed_knowledge(existing[0])
        return existing[0]
    rid = str(uuid.uuid4())
    root = Path(db.DATA_DIR) / 'repositories' / rid
    root.mkdir(parents=True)
    for name, content in FILES.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8',newline='')
    git(root, 'init', '-b', 'main')
    git(root, 'add', '.')
    git(root, '-c', 'user.name=Steward demo', '-c', 'user.email=demo@localhost', 'commit', '-m', 'Reviewed sample baseline')
    origin={'source':'fixture://parcel','branch':'main','commit':git(root,'rev-parse','HEAD').strip()}
    source=snapshots.capture(rid,root,origin)
    index = repository.index_repository(root,repo_id=rid,source_snapshot_id=source['id'])
    index.update(repo_id=rid,source_snapshot_id=source['id'])
    repo = db.put('repositories', dict(id=rid, name='Parcel · sample', root=str(root), is_sample=True, snapshot=index['snapshot'], source_snapshot_id=source['id'],origin=origin,index=index, branch='main',commit=origin['commit'], policies={s:'approve_first' for s in ['ticket','request','security','analysis','research']}, monitoring=False, poll_interval=300))
    seed_jobs(repo)
    seed_knowledge(repo)
    return repo

def seed_knowledge(repo):
    from . import db
    identity=repo['id']+'-integer-cents'
    if not db.get('knowledge',identity):
        db.put('knowledge',{'id':identity,'repo_id':repo['id'],'title':'Money uses integer cents','content':'Use integer cents; never floating-point money. Empty orders return zero.','scope':'*','status':'observed','evidence':'README.md:3-4','last_validated_snapshot':repo['snapshot'],'version':1,'author':'Sample repository observation'})

def seed_jobs(repo):
    from . import db
    for scenario, title, source, kind, description in SCENARIOS:
        identity=hashlib.sha256((repo['id']+':fixture:'+scenario).encode()).hexdigest()
        if db.get('jobs',identity):
            if scenario=='security':
                db.mutate('jobs',identity,lambda j:{**j,'source_key':'STEWARD-DEMO-001:fixture-parser:1.0.0','source_url':'fixture:STEWARD-DEMO-001'})
            continue
        db.put('jobs', dict(id=identity,repo_id=repo['id'], title=title, description=description, source=source, source_key='STEWARD-DEMO-001:fixture-parser:1.0.0' if scenario=='security' else None,source_url='fixture:STEWARD-DEMO-001' if scenario=='security' else 'fixture://parcel/'+scenario, type=kind, priority='high' if scenario=='bug' else 'medium', risk='low', status='Awaiting approval', requires_approval=True, preparation_approved=False, base_snapshot=repo['snapshot'], scenario=scenario, approach='Inspect source-linked context, prepare an isolated patch, and run reviewed fixture checks.', acceptance_criteria=description, affected_areas=['app.py','pricing.py','config.py','tests/test_quote.py'] if scenario in ('bug','feature') else ['search.py'] if scenario=='performance' else ['requirements.lock'], approval=None))

def fixture_response(job, files, decisions):
    """Only this provider contains scenario-specific editing logic."""
    scenario = job.get('scenario')
    edits = []
    if scenario == 'bug':
        edits.append({'path':'pricing.py','content':files['pricing.py'].replace('item["price_cents"] for item', 'item["price_cents"] * item.get("quantity", 1) for item')})
        test = '\n    def test_quantity(self):\n        self.assertEqual(quote([{ "price_cents": 100, "quantity": 2}])["total_cents"], 220)\n'
        edits.append({'path':'tests/test_quote.py','content':FILES['tests/test_quote.py'] + test})
    elif scenario == 'feature':
        selected = next((d.get('selected') for d in decisions if d.get('selected')), None)
        if not selected:
            return {'decision':{'title':'Oversized order behaviour', 'options':[{'id':'strict','label':'Reject oversized orders','tradeoff':'Explicit error; caller must split the order.'},{'id':'lenient','label':'Truncate to the configured limit','tradeoff':'Returns a quote for the first items only.'}], 'recommendation':'strict'}}
        behavior = '    if len(items) > MAX_ITEMS:\n        raise ValueError("Order exceeds MAX_ITEMS")\n' if selected=='strict' else '    items = items[:MAX_ITEMS]\n'
        edits.append({'path':'app.py','content':'from pricing import total\nfrom config import TAX_RATE, MAX_ITEMS\n\ndef quote(items):\n'+behavior+'    return {"total_cents": total(items, TAX_RATE)}\n'})
        test = '\n    def test_limit(self):\n        with self.assertRaises(ValueError):\n            quote([{ "price_cents": 100 }] * 101)\n' if selected=='strict' else '\n    def test_limit(self):\n        self.assertEqual(quote([{ "price_cents": 100 }] * 101)["total_cents"], 11000)\n'
        edits.append({'path':'tests/test_quote.py','content':FILES['tests/test_quote.py']+test})
    elif scenario == 'security':
        edits.append({'path':'requirements.lock','content':files['requirements.lock'].replace('1.0.0','1.0.1')})
    elif scenario == 'performance':
        edits.append({'path':'search.py','content':'def unique_labels(labels):\n    return list(dict.fromkeys(labels))\n'})
    else:
        raise ValueError('Offline sample mode supports only the four labelled bundled scenarios; configure a live provider for this request.')
    if job.get('revision_instructions'):
        instructions=job['revision_instructions']
        instructions='\n'.join(instructions) if isinstance(instructions,list) else instructions
        if scenario=='bug':
            tests=next(e for e in edits if e['path']=='tests/test_quote.py')
            tests['content']+='\n    def test_zero_quantity(self):\n        self.assertEqual(quote([{ "price_cents": 100, "quantity": 0}])["total_cents"], 0)\n    def test_default_quantity(self):\n        self.assertEqual(quote([{ "price_cents": 100 }])["total_cents"], 110)\n'
        edits.append({'path':'CHANGE_NOTES.md','content':'# Demo revision\n\nFixture revision adds reviewed zero/default-quantity regression coverage for the bug scenario. Arbitrary natural-language changes require a live provider. Requested revision:\n\n'+instructions+'\n'})
    return {'edits':edits, 'explanation':'Reviewed offline fixture: '+job['description']}

def trusted_tree(root):
    """Execution is allowed only for byte-identical reviewed sample baseline."""
    actual = {p.relative_to(root).as_posix():p.read_text(encoding='utf-8') for p in Path(root).rglob('*') if p.is_file() and '.git' not in p.parts and '__pycache__' not in p.parts}
    return actual == FILES
