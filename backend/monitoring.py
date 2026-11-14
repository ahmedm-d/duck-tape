"""Persistent polling schedule; external work belongs to the operations worker."""
from datetime import datetime, timedelta, timezone
from . import db, operations


def schedule_due(now):
    queued=[]
    for repo in db.list_records('repositories'):
        if not repo.get('monitoring') or (repo.get('next_scan') and repo['next_scan']>now):
            continue
        operation=operations.submit('scan',repo['id'],{})
        db.mutate('repositories',repo['id'],lambda r:{**r,'last_attempt':now,'monitoring_operation':operation['id'],
            'next_scan':(datetime.now(timezone.utc)+timedelta(seconds=r.get('poll_interval',300))).isoformat()})
        queued.append(operation['id'])
    return queued
