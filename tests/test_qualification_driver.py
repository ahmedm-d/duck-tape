"""Controller-only driver tests; imported behavior is evaluated only in Docker."""
import json
from pathlib import Path
import pytest
from scripts import qualification_driver as driver

@pytest.mark.parametrize('language',['python','typescript'])
def test_manifest_behavior_contract_covers_bugs_and_greeting_journeys(language):
    bug=driver.behavior_cases({'language':language,'journey':'bug-regression','expected_behavior':{'average_empty':0,'average_2_4':3}})
    assert bug=={'average_empty':0,'average_2_4':3,'default_greeting':'Hello world','named_greeting':'Hello Ada'}
    feature=driver.behavior_cases({'language':language,'journey':'decision-feature','expected_behavior':{'selected_option_label':'Welcome','formal_greeting':'Welcome Ada','default_greeting':'Hello world'}})
    assert feature['formal_greeting']=='Welcome Ada' and feature['named_greeting']=='Hello Ada'
    revision={'language':language,'journey':'revision','expected_behavior':{'first_default':'Hello Steward','revision_default':'Hello Team'}}
    assert driver.behavior_cases(revision,'first')['default_greeting']=='Hello Steward'
    assert driver.behavior_cases(revision)['default_greeting']=='Hello Team'

def test_independent_behavior_requires_actual_values_not_only_passing_exit_code():
    expected={'average_empty':0,'average_2_4':3}
    passing={'status':'passed','exit_code':0,'stdout':'STEWARD_BEHAVIOR='+json.dumps(expected)}
    assert driver.behavior_matches(passing,expected)
    for output in ['OK','STEWARD_BEHAVIOR={"average_empty":0}', 'STEWARD_BEHAVIOR={"average_empty":0,"average_2_4":0}']:
        assert not driver.behavior_matches({**passing,'stdout':output},expected)
    assert not driver.behavior_matches({**passing,'status':'failed'},expected)

def test_budget_sums_provider_attempt_history_and_keeps_unknown_tokens_unknown():
    stages=[{'telemetry':{'attempts':2,'mode':'live','usage':{'output_tokens':11}},'attempt_history':[{'attempts':1,'mode':'live','usage':None}]}]
    budget=driver.budget_report(stages,{'max_provider_calls':2,'max_output_tokens':30000})
    assert budget['provider_calls']==3 and not budget['within_budget']
    assert budget['output_tokens'] is None and budget['known_output_tokens']==11

def test_source_immutability_checks_bytes_and_added_paths(tmp_path):
    (tmp_path/'source.py').write_bytes(b'line\r\n')
    assert driver.source_unchanged(tmp_path,{'source.py':b'line\r\n'})
    (tmp_path/'source.py').write_bytes(b'line\n')
    assert not driver.source_unchanged(tmp_path,{'source.py':b'line\r\n'})
    (tmp_path/'source.py').write_bytes(b'line\r\n');(tmp_path/'extra.py').write_text('added')
    assert not driver.source_unchanged(tmp_path,{'source.py':b'line\r\n'})

def test_decision_selection_rejects_ambiguous_welcome_choices():
    assert driver.select_option({'options':[{'id':'a','label':'Formal: Welcome'},{'id':'b','label':'Good day'}]},'Welcome')=='a'
    with pytest.raises(ValueError):driver.select_option({'options':[{'id':'a','label':'Welcome'},{'id':'b','label':'Welcome too'}]},'Welcome')

def test_unknown_journey_is_not_silently_treated_as_a_bug():
    with pytest.raises(ValueError,match='Unsupported'):driver.behavior_cases({'language':'python','journey':'invented','expected_behavior':{}})

def test_unavailable_provider_blocks_without_inventing_attempts(monkeypatch):
    monkeypatch.setattr(driver.providers,'configuration',lambda:{'model':'real-model'})
    monkeypatch.setattr(driver.providers,'health',lambda:{'available':False,'model':'real-model','mode':'offline sample'})
    monkeypatch.setattr(driver.sandbox,'health',lambda:{'available':True,'probes':{'network':True}})
    result=driver.driver({'id':'python-bug-regression','journey':'bug-regression','language':'python'})
    assert result['status']=='blocked' and not result['passed'] and result['attempts']==[]
    assert result['evidence'][0]['available'] is False

def test_unsafe_review_needs_concrete_valid_source_reference():
    wrong={'service.py':'def average(values):\n    return 0\n'}
    finding={'message':'The constant average is wrong for nonempty input','reference':{'path':'service.py','start_line':2,'end_line':2}}
    assert driver.review_rejects_unsafe({'approved':False,'findings':[finding]},wrong)
    assert not driver.review_rejects_unsafe({'approved':True,'findings':[finding]},wrong)
    assert not driver.review_rejects_unsafe({'approved':False,'findings':[{**finding,'reference':{'path':'missing.py','start_line':1,'end_line':1}}]},wrong)


def test_wait_approves_persisted_plan_and_records_exact_reviewed_steps(monkeypatch,tmp_path):
    context=driver.Qualification({'id':'approval','budget':{}},tmp_path)
    context.job={'id':'job'}
    plan={'id':'job','status':'Awaiting approval','plan_hash':'actual-plan','record_version':4,'approach':[{'paths':['service.py'],'description':'Fix empty average'}]}
    states=iter([plan,{'id':'job','status':'Ready for review'}])
    monkeypatch.setattr(driver.db,'get',lambda kind,identity:next(states))
    calls=[]
    monkeypatch.setattr(context,'api',lambda path,body:calls.append((path,body)))
    monkeypatch.setattr(driver.time,'sleep',lambda seconds:None)
    assert context.wait({'Ready for review'})['status']=='Ready for review'
    assert calls==[('/jobs/job/approve',{'expected_version':4})]
    assert context.evidence[0]['plan_hash']=='actual-plan'
    assert context.evidence[0]['steps']==plan['approach']


def test_plan_consent_without_an_actual_plan_is_not_recorded_as_plan_approval(tmp_path):
    context=driver.Qualification({'id':'approval','budget':{}},tmp_path)
    context.job={'id':'job'}
    assert not context.approve_plan({'id':'job','status':'Awaiting approval','approach':'Investigate'})
    assert context.evidence==[]


def test_wait_refuses_to_approve_hash_without_actual_steps(monkeypatch,tmp_path):
    context=driver.Qualification({'id':'approval','budget':{}},tmp_path)
    context.job={'id':'job'}
    monkeypatch.setattr(driver.db,'get',lambda kind,identity:{'id':'job','status':'Awaiting approval','plan_hash':'hash','approach':'Investigate'})
    with pytest.raises(ValueError,match='steps'):context.wait({'Ready for review'})
