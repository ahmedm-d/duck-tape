"""Reporter regressions derived from actual Jest output; no live acceptance claim."""
from backend import worker


def test_exact_jest_target_pass_is_not_blocked_by_unselected_tests():
    profile={'profile':'node-npm','commands':[{'argv':['./node_modules/.bin/jest'],'purpose':'test'}]}
    record={'status':'passed','test_count':3,'skipped_count':2,'stdout':'PASS ./service.test.js\n  ✓ requested behavior (3 ms)\n  ○ skipped requested behavior extra\n  ○ skipped unrelated skip\nTests: 2 skipped, 1 passed, 3 total\n'}
    assert worker.acceptance_observed(record,{'test':'requested behavior'},profile)
    record['stdout']='  ✓ requested behavior extra (3 ms)\n  ○ skipped requested behavior\n'
    assert not worker.acceptance_observed(record,{'test':'requested behavior'},profile)
    record['stdout']='  ○ skipped requested behavior\n'; record['skipped_count']=3
    assert not worker.acceptance_observed(record,{'test':'requested behavior'},profile)
