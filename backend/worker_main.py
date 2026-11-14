"""Separate worker process supervised by run.py; API handlers never own its lifetime."""
import os
import signal
import threading
from . import db, worker


def main():
    db.init()
    stop = threading.Event()
    instance_id=os.getenv('STEWARD_INSTANCE_ID','')
    for name in ('SIGINT', 'SIGTERM'):
        signal.signal(getattr(signal,name),lambda *args:stop.set())
    from . import operations, sandbox
    operations.start_operations()
    try:
        reconciliation = sandbox.reconcile()
    except Exception as exc:
        reconciliation = {'error':type(exc).__name__}
    worker.start_worker()
    try:
        while not stop.wait(2):
            shutdown=db.get('system','shutdown:'+instance_id) if instance_id else None
            if shutdown and shutdown.get('requested'):
                break
            db.put('services',{'id':'worker','pid':os.getpid(),'instance_id':instance_id,'heartbeat':db.now(),'status':'running','reconciliation':reconciliation})
    finally:
        worker.stop_worker()
        operations.stop_operations()
        db.put('services',{'id':'worker','pid':os.getpid(),'instance_id':instance_id,'heartbeat':db.now(),'status':'stopped'})


if __name__ == '__main__':
    main()
