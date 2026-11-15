#!/usr/bin/env python3
"""Offline Adaption chart-metric extraction from Chrome DevTools HAR.

Usage: python extract_adaption_har.py network.har --output adaption_har_export
HAR files can contain credentials: DO NOT SHARE THE HAR; only share sanitized CSVs.
"""
import argparse, base64, csv, json, re
from pathlib import Path

METRIC = re.compile(r'(grad(?:ient)?[_\s/-]*norm|train[_/ -]*loss|val(?:idation)?[_/ -]*loss|eval[_/ -]*loss|learning[_/ -]*rate)', re.I)
GRAD = re.compile(r'grad(?:ient)?[_\s/-]*norm', re.I)
STEPS = ('step','global_step','training_step','iteration','epoch','x')
VALUES = ('value','y','scalar','metric_value')

def gather(obj, source, path='$', hint=None, step=None):
    rows = []
    def append(name, s, v, p):
        if isinstance(v,(int,float)) and not isinstance(v,bool):
            rows.append(dict(source=source, path=p, metric=name, step=s if s is not None else '', value=v))
    if isinstance(obj,dict):
        named = next((obj[k] for k in ('metric','metric_name','name','tag','key','series') if isinstance(obj.get(k),str) and METRIC.search(obj[k])),None)
        hint = named or hint
        step = next((obj[k] for k in STEPS if isinstance(obj.get(k),(int,float,str))),step)
        for k,v in obj.items():
            if METRIC.search(k):
                if isinstance(v,(int,float)): append(k,step,v,f'{path}.{k}')
                # chart-array format: {"gradient_norm": [1,2,3]}
                elif isinstance(v,list) and all(isinstance(n,(int,float)) for n in v):
                    for i,n in enumerate(v): append(k,i,n,f'{path}.{k}[{i}]')
        if hint:
            for k in VALUES:
                if isinstance(obj.get(k),(int,float)):
                    append(hint,step,obj[k],f'{path}.{k}')
                    break
        for k,v in obj.items():
            rows.extend(gather(v,source,f'{path}.{k}', k if METRIC.search(k) else hint, step))
    elif isinstance(obj,list):
        for i,v in enumerate(obj):
            if hint and isinstance(v,(list,tuple)) and len(v)==2 and isinstance(v[1],(int,float)):
                append(hint,v[0],v[1],f'{path}[{i}]')
            elif hint and isinstance(v,(int,float)):
                append(hint,i,v,f'{path}[{i}]')
            else:
                rows.extend(gather(v,source,f'{path}[{i}]',hint,step))
    return rows

def main():
    p=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('har',type=Path)
    p.add_argument('--output',type=Path,default=Path('adaption_har_export'))
    args=p.parse_args()
    data=json.loads(args.har.read_text(encoding='utf-8'))
    entries=data.get('log',{}).get('entries',[])
    rows=[]; examined=0; candidates=0
    for i,e in enumerate(entries):
        c=e.get('response',{}).get('content',{})
        raw=c.get('text')
        if not raw: continue
        if c.get('encoding')=='base64':
            try: raw=base64.b64decode(raw).decode('utf-8')
            except Exception: continue
        examined+=1
        try: obj=json.loads(raw)
        except (ValueError,TypeError):continue
        # Never export the original HAR responses: they can contain API tokens or account data.
        match=gather(obj,f'HAR-entry-{i}')
        rows.extend(match)
        if match: candidates+=1
    args.output.mkdir(parents=True,exist_ok=True)
    # Dedup if common recursive format yields same value twice.
    rows=list({(r['source'],r['path'],r['metric'],str(r['step']),r['value']):r for r in rows}.values())
    for name, subset in [('all_metrics.csv',rows),('gradient_norm.csv',[r for r in rows if GRAD.search(r['metric']) and r['step']!=''])]:
        with (args.output/name).open('w',newline='',encoding='utf-8') as f:
            writer=csv.DictWriter(f,fieldnames=['source','path','metric','step','value'])
            writer.writeheader(); writer.writerows(subset)
        print(f'{name}: {len(subset)} rows')
    print(f'Inspected {examined} response bodies in {len(entries)} HAR entries; {candidates} contained recognized metric values.')
    if not rows: print('No metric series found. The chart may use WebSockets, binary responses, or nonstandard metric field names.')

if __name__=='__main__':main()
