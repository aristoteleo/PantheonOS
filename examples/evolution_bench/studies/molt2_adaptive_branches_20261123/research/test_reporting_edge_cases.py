from pathlib import Path
import json,runpy
HERE=Path(__file__).resolve().parent

def test_reward_audit_handles_evaluation_failure_without_measurement(tmp_path):
    (tmp_path/'workflow.json').write_text('{"status":"complete"}')
    (tmp_path/'store.json').write_text('{"individuals":[]}')
    row={'batch_id':'failed','arm':'chain:0','reason':'incumbent_floor','frontier':1.,'evaluations':1,'best_child':None,'success':0}
    (tmp_path/'method.json').write_text(json.dumps({'state':{'adaptive_branches':{'decisions':[row],'history':[row]}}}))
    result=runpy.run_path(str(HERE/'reward_sensitivity.py'))['audit'](tmp_path)
    assert result['joined_candidate_evaluations']==1
    assert result['native_window_fallback_batches']==['failed']
    assert all(r['positive_work_items']==0 for r in result['thresholds'])

def test_legacy_duplicate_event_requires_review_but_fixed_code_does_not():
    inspect=runpy.run_path(str(HERE/'measurement_event_audit.py'))['classify']
    row={'id':'same','measurements':[{'ok':True,'batch_id':'a','metrics':{'combined_score':1.}},{'ok':True,'batch_id':'a','metrics':{'combined_score':1.}}]}
    assert inspect([row],True)['legacy_event_review_required']
    assert not inspect([row],False)['legacy_event_review_required']
    row['measurements'][1]['batch_id']='b'
    assert not inspect([row],True)['legacy_event_review_required']
    row['measurements'][1]['metrics']['combined_score']=1.1
    assert inspect([row],True)['legacy_event_review_required']

def test_optional_diagnostic_error_is_reported_not_raised():
    optional=runpy.run_path(str(HERE/'paired_inference.py'))['optional'];errors=[]
    def broken():raise ValueError('missing trace')
    result=optional('example',broken,errors)
    assert result['status']=='error' and errors[0]['diagnostic']=='example'

def test_paired_statistics_keep_exact_sign_and_family_correction():
    analyze=runpy.run_path(str(HERE/'paired_inference.py'))['analyze'];rows=[]
    for task in ['ahc024','ahc058']:
        for method in ['simpletes','molt','molt_modified','molt2_adaptive_branches']:
            for seed in range(400,405):rows.append({'task':task,'method':method,'seed':seed,'status':'complete','score':2. if method=='molt2_adaptive_branches' else 1.})
    result=analyze(rows)
    assert all(x['one_sided_sign_p']==1/32 and x['holm_adjusted_p']==6/32 for x in result['comparisons'])
    rows[0]['status']='failed';result=analyze(rows)
    assert result['comparisons'][0]['status']=='incomplete_withheld'
