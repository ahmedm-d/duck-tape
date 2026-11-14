"""Fixed relevant-file labels are defined independently of retrieval output."""
import json
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.repository import index_repository
from backend.retrieval import retrieve
from backend.sample import FILES
from backend import db,providers


def score_evidence(items, labels):
    expected = {path for path, _, _ in labels}
    found = {item['path'] for item in items}
    recalled = sum(any(item['path'] == path and item['start_line'] <= first <= last <= item['end_line'] for item in items) for path, first, last in labels)
    useful = sum(any(item['path'] == path and item['start_line'] <= last and first <= item['end_line'] for path, first, last in labels) for item in items)
    return {'file_recall': len(found & expected) / len(expected) if expected else 0,
            'file_precision': len(found & expected) / len(found) if found else 0,
            'range_recall': recalled / len(labels) if labels else 0,
            'range_precision': useful / len(items) if items else 0}


def evaluate(output=None):
    root = Path(__file__).resolve().parents[1]
    fixture = root / 'data' / 'evaluation' / str(uuid.uuid4())
    fixture.mkdir(parents=True)
    source = {**FILES,
              'ts/config.ts': 'export const TAX = 10;\n',
              'ts/helper.ts': 'import { TAX } from "./config.js";\nexport function totalTS(value: number) { return value + TAX; }\n',
              'ts/api.ts': 'import { totalTS } from "./helper.js";\nexport function quoteTS(value: number) { return totalTS(value); }\n',
              'ts/api.test.ts': 'import { quoteTS } from "./api.js";\nexport const observed = quoteTS(100);\n'}
    for path, content in source.items():
        destination = fixture / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding='utf-8')
    index = index_repository(fixture,repo_id='evaluation:'+fixture.name)
    db.init()
    tasks = [
        ('quote', [('app.py',4,5), ('pricing.py',1,3), ('config.py',1,2), ('tests/test_quote.py',4,8)]),
        ('unique_labels', [('search.py',1,6), ('tests/test_search.py',4,6)]),
        ('MAX_ITEMS', [('config.py',2,2), ('app.py',1,5), ('tests/test_quote.py',4,8)]),
        ('quoteTS', [('ts/api.ts',1,2), ('ts/helper.ts',1,2), ('ts/config.ts',1,1), ('ts/api.test.ts',1,2)]),
    ]
    rows = []
    for query, relevant in tasks:
        for requested, graph, semantic in [('keyword',False,False), ('graph',True,False), ('semantic',True,True)]:
            if semantic and not providers.configuration()['embedding_model']:
                rows.append({'query': query, 'requested_mode': requested, 'status': 'unavailable',
                             'reason': 'No actual embedding model configured', 'model_usage': None})
                continue
            previous={item['id'] for item in db.list_records('embedding_runs')}
            result = retrieve(index,query,graph=graph,semantic=semantic,budget_tokens=10000)
            calls=[item for item in db.list_records('embedding_runs') if item['id'] not in previous and item['repo_id']==index['repo_id']]
            rows.append({'query':query,'requested_mode':requested,'mode':result['mode'],'status':'measured',
                         'expected_ranges':relevant,'found_ranges':[(item['path'],item['start_line'],item['end_line']) for item in result['items']],
                         **score_evidence(result['items'],relevant), 'context_bytes':len(json.dumps(result,ensure_ascii=False).encode()),
                         'token_upper_bound':result['token_estimate'],'latency_ms':result['latency_ms'],
                         'embedding_calls':len(calls),'model_usage':[item['usage'] for item in calls] or None})
    report = {'fixture':'reviewed Python Parcel sample plus fixed TS module chain; labels defined independently of retrieval',
              'snapshot':index['snapshot'],'results':rows,'task_check_success':'Not measured by retrieval benchmark; see worker acceptance tests.',
              'usage_note':'Unknown usage is null. Unavailable semantic runs are separate from deterministic fallback measurements.',
              'range_precision_definition':'Fraction of returned source ranges that overlap at least one independently labelled relevant range.',
              'warning':'Small fixed tasks measure retrieval only. Precision includes extra results; no quality, task-success or latency improvement is claimed.'}
    output = Path(output) if output else root / 'docs' / 'retrieval-evaluation.json'
    output.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))
    return report


if __name__ == '__main__':
    evaluate()
