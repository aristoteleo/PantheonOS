"""Offline matched-factorial audit, callback, allowance and checkpoint tests."""
import asyncio,copy,json
import pytest
from pantheon.evolution.methods.molt2_factorial import Molt2Factorial,Molt2FactorialVariator,toggle_factorial,factorial_arm,hypotheses
from pantheon.evolution.core.work import Failure,Produced
from pantheon.evolution.core.genome import CodeGenome
from test_molt2 import make,run,measured,resolve_base

PY='''# MOLT2_HYPOTHESIS_A: broad representation change
# with a second line preserved in memory.
# MOLT2_HYPOTHESIS_B: complementary search mechanism
# whose benefit may depend on the representation.
# MOLT2_FALSIFIER: no positive conditional gain.
MOLT2_A = 0
MOLT2_B = 0
def solve(x):
    return x + MOLT2_A + 2*MOLT2_B + MOLT2_A*MOLT2_B
'''
CPP='''// MOLT2_HYPOTHESIS_A: representation
// multiline continuation
// MOLT2_HYPOTHESIS_B: complementary mechanism
static constexpr bool MOLT2_A = false;
static constexpr bool MOLT2_B = false;
int solve(int x) { return x + MOLT2_A + 2*MOLT2_B; }
'''
def fixture(**kw):
    _,ctx,seed=make();m=Molt2Factorial(num_chains=2,k_candidates=2,num_inspirations=2,selector='rpucg',experiment_interval=1,warmup=2,experiment_fraction=.6,**kw);run(m.start(ctx,[seed]));return m,ctx,seed

def experiment(m,ctx):
    for _ in range(20):
        items=run(m.ask(ctx,2));resolve_base(m,ctx,items)
        for item in items:
            if item.meta.get('molt2_experiment'):return item
    raise AssertionError('No experiment')

def resolve(m,ctx,item,values=None,order=('11','01','10','00')):
    arms,_=toggle_factorial({'solution.py':PY},'solution.py');values=values or {'00':.6,'10':.7,'01':.8,'11':1.0}
    for a in order:
        ind,measurement=measured(ctx,item,arms[a],score=values[a]);run(m.on_measured(ctx,ind,measurement))
    return arms

@pytest.mark.parametrize('path,code',[('solution.py',PY),('solution.cpp',CPP)])
def test_four_exact_source_states(path,code):
    arms,h=toggle_factorial({path:code,'data.txt':'fixed'},path)
    assert len(h)==64 and len({tuple(x.items()) for x in arms.values()})==4
    for label,files in arms.items():
        assert factorial_arm(files,path)==label and files['data.txt']=='fixed'
        # Canonicalize both literals, requiring exact byte equality everywhere else.
        canonical,_=toggle_factorial(files,path)
        assert canonical==arms

@pytest.mark.parametrize('bad',[PY.replace(' + 2*MOLT2_B + MOLT2_A*MOLT2_B',''),PY+'\nMOLT2_A=1\n',PY+'\ndef f(MOLT2_B): return MOLT2_B\n',PY.replace('MOLT2_A = 0','"""\nMOLT2_A = 0\n"""'),CPP.replace('static constexpr bool MOLT2_A','bool MOLT2_A'),CPP+'\nint f(){bool MOLT2_A=false;return MOLT2_A;}'])
def test_reject_missing_unused_or_mutable_flags(bad):
    with pytest.raises(ValueError):toggle_factorial({'solution.py' if bad.startswith('#') else 'solution.cpp':bad},'solution.py' if bad.startswith('#') else 'solution.cpp')

def test_multiline_annotation_not_truncated():
    h=hypotheses(PY,'solution.py');assert 'second line preserved' in h['HYPOTHESIS_A'];assert 'whose benefit' in h['HYPOTHESIS_B']
    assert 'multiline continuation' in hypotheses(CPP,'solution.cpp')['HYPOTHESIS_A']

def test_reverse_callbacks_interaction_and_source_memory():
    m,ctx,_=fixture();item=experiment(m,ctx);assert item.k==4
    arms=resolve(m,ctx,item);e=m.evidence[-1]
    assert e['status']=='observed_complete_factorial';assert e['effects']['main_a']==pytest.approx(.1)
    assert e['effects']['main_b']==pytest.approx(.2);assert e['effects']['interaction']==pytest.approx(.1)
    assert e['executable_states']==arms;assert all(len(s['source_sha256'])==64 for s in e['states'].values())
    assert len(e['context_sha256'])==64 and 'second line preserved' in m._memory()

def test_missing_score_is_not_zero():
    m,ctx,_=fixture();item=experiment(m,ctx);resolve(m,ctx,item,{'00':.6,'10':.7,'01':None,'11':.9});e=m.evidence[-1]
    assert e['status']=='inconclusive';assert e['effects']['main_b'] is None and e['effects']['interaction'] is None
    assert e['effects']['main_a']==pytest.approx(.1)

def test_confounded_anchor_and_negative_retained():
    m,ctx,_=fixture();item=experiment(m,ctx);resolve(m,ctx,item,{'00':.3,'10':.2,'01':.1,'11':.25});e=m.evidence[-1]
    assert e['status']=='confounded_control';assert e['effects']['main_a']<0;assert 'confounded_control' in m._memory()

def test_generation_failure_closes_four_without_zero():
    m,ctx,_=fixture();item=experiment(m,ctx)
    for _ in range(4):run(m.on_failed(ctx,Failure(item_id=item.id,batch_id=item.batch_id,stage='generate',reason='not produced')))
    assert not m.pending_experiments;assert m.evidence[-1]['scores']==dict.fromkeys(['00','10','01','11'])
    assert m.evidence[-1]['effects']['interaction'] is None

def test_cost_share_reserves_four_and_only_one_open():
    m,ctx,_=fixture();item=experiment(m,ctx)
    assert m.experiment_eval_slots==4
    assert m.experiment_eval_slots/(m.base_eval_slots+m.experiment_eval_slots)<=m.experiment_fraction
    assert not any(x.meta.get('molt2_experiment') for x in run(m.ask(ctx,2)))

def test_eval_limit_reserves_seed_and_all_four_arms():
    m,ctx,_=fixture();m.max_eval_calls=8
    while True:
        items=run(m.ask(ctx,2))
        if not items:break
        assert not any(x.meta.get('molt2_experiment') for x in items)
        resolve_base(m,ctx,items)
    assert m.base_eval_slots+m.seed_eval_slots==8

def test_resume_preserves_all_observed_source_states_without_retry():
    m,ctx,_=fixture();item=experiment(m,ctx);resolve(m,ctx,item,order=('11','00'))
    state=json.loads(json.dumps(m.state_dict()));new,_,_=fixture();new.load_state_dict(state);new.reconcile(ctx)
    e=new.evidence[-1];assert e['status']=='interrupted_unresolved' and len(e['executable_states'])==2
    assert not new.pending_experiments;assert new.experiment_eval_slots==m.experiment_eval_slots

def test_one_model_template_four_evaluations(monkeypatch):
    from pantheon.evolution.methods.simpletes import UpstreamCompletionVariator
    m,ctx,_=fixture();item=experiment(m,ctx);calls=[]
    async def fake(self,ctx,item):
        calls.append(item.k);return [Produced(item_id=item.id,genome=CodeGenome(files={'solution.py':PY}))]
    monkeypatch.setattr(UpstreamCompletionVariator,'create',fake)
    v=Molt2FactorialVariator.__new__(Molt2FactorialVariator);v.target_file='solution.py'
    out=run(v.create(ctx,item))
    assert calls==[1] and len(out)==4


def test_four_programs_execute_distinct_behaviors():
    arms,_=toggle_factorial({'solution.py':PY},'solution.py');observed={}
    for a,files in arms.items():
        namespace={};exec(files['solution.py'],namespace);observed[a]=namespace['solve'](0)
    assert observed=={'00':0,'10':1,'01':2,'11':4}

def test_evaluate_failure_preserves_source_and_duplicate_callback(monkeypatch):
    m,ctx,_=fixture();item=experiment(m,ctx);arms,_=toggle_factorial({'solution.py':PY},'solution.py')
    ind,measurement=measured(ctx,item,arms['10'],score=None,ok=False,artifacts={'evaluation_error':'timeout'})
    f=Failure(item_id=item.id,batch_id=item.batch_id,stage='evaluate',reason='timeout',detail={'individual_id':ind.id})
    run(m.on_failed(ctx,f));run(m.on_failed(ctx,f));assert m.pending_experiments[item.batch_id]['resolved']==1
    resolve(m,ctx,item,order=('11','01','00'));e=m.evidence[-1]
    assert e['scores']['10'] is None and e['effects']['main_a'] is None
    assert e['executable_states']['10']==arms['10'] and 'timeout' in e['errors']

def test_core_driver_measures_all_four_normal_arms(monkeypatch):
    from pantheon.evolution.methods.simpletes import UpstreamCompletionVariator
    from pantheon.evolution.core.loop import _run_create
    from pantheon.evolution.core.work import Measurement
    m,ctx,_=fixture();item=experiment(m,ctx);wire=[];eval_calls=[]
    async def fake(self,ctx,item):
        wire.append(item.k);return [Produced(item_id=item.id,genome=CodeGenome(files={'solution.py':PY}))]
    monkeypatch.setattr(UpstreamCompletionVariator,'create',fake)
    class Evaluator:
        async def measure(self,ctx,ind,fidelity):
            arm=factorial_arm(ind.genome.files,'solution.py');eval_calls.append(arm)
            return Measurement(individual_id=ind.id,fidelity=fidelity,metrics={'combined_score':{'00':.6,'10':.7,'01':.8,'11':1.0}[arm],'validity':1})
    v=Molt2FactorialVariator.__new__(Molt2FactorialVariator);v.target_file='solution.py'
    outcomes=run(_run_create(item,ctx,v,{'code':Evaluator()}))
    for ind,measurement in reversed(outcomes):run(m.on_measured(ctx,ind,measurement))
    assert wire==[1] and eval_calls==['00','10','01','11']
    assert m.evidence[-1]['effects']['interaction']==pytest.approx(.1)
    assert not m.pending_experiments

def test_complete_checkpoint_roundtrip_keeps_full_code():
    m,ctx,_=fixture();item=experiment(m,ctx);resolve(m,ctx,item)
    state=json.loads(json.dumps(m.state_dict()));new,_,_=fixture();new.load_state_dict(state)
    assert new.evidence==m.evidence and new.base_eval_slots==m.base_eval_slots
    assert new.evidence[-1]['executable_states']['11']['solution.py']

def test_mismatched_templates_never_receive_effect_credit():
    m,ctx,_=fixture();item=experiment(m,ctx);arms,_=toggle_factorial({'solution.py':PY},'solution.py')
    for arm,files in arms.items():
        if arm=='11':files={**files,'solution.py':files['solution.py']+'\n# unrelated extra rewrite\n'}
        ind,measurement=measured(ctx,item,files,score=.6 if arm=='00' else .9)
        run(m.on_measured(ctx,ind,measurement))
    e=m.evidence[-1]
    assert not e['two_switch_source_contrast'] and e['status']=='inconclusive'
    assert all(v is None for v in e['effects'].values())

def test_eval_cap_includes_pending_four_arm_reservation():
    m,ctx,_=fixture();m.max_eval_calls=13;seen=0
    for _ in range(20):
        items=run(m.ask(ctx,2))
        if not items:break
        resolve_base(m,ctx,items)
        for item in items:
            if item.meta.get('molt2_experiment'):resolve(m,ctx,item);seen+=1
    assert seen==1 and m.seed_eval_slots+m.base_eval_slots+m.experiment_eval_slots==13
    assert not m.pending_experiments and not m.open

@pytest.mark.parametrize('directive',[
 '#if MOLT2_A\nint a=1;\n#endif',
 '#ifdef MOLT2_B\nint b=1;\n#endif',
 '#if defined(MOLT2_A)\nint a=1;\n#endif',
 '#define AXIS MOLT2_A',
 '#if MOLT2_\\\nA\nint a=1;\n#endif',
 '%:if MOLT2_B\nint b=1;\n%:endif',
])
def test_reject_cpp_preprocessor_flag_uses(directive):
    code='static constexpr bool MOLT2_A = false;\nstatic constexpr bool MOLT2_B = false;\n'+directive+'\nint f(){ return MOLT2_A + MOLT2_B; }\n'
    with pytest.raises(ValueError,match='preprocessor'):
        toggle_factorial({'solution.cpp':code},'solution.cpp')

def test_reject_flags_used_only_in_preprocessor():
    code='static constexpr bool MOLT2_A = false;\nstatic constexpr bool MOLT2_B = false;\n#if MOLT2_A\nint a=1;\n#endif\n#ifdef MOLT2_B\nint b=1;\n#endif\n'
    with pytest.raises(ValueError,match='preprocessor'):
        toggle_factorial({'solution.cpp':code},'solution.cpp')

def test_normal_cpp_conditionals_accepted():
    code='static constexpr bool MOLT2_A = false;\nstatic constexpr bool MOLT2_B = false;\nint f(){ int x=0; if constexpr(MOLT2_A) x+=1; return x+(MOLT2_B?2:0); }\n'
    arms,_=toggle_factorial({'solution.cpp':code},'solution.cpp')
    assert len(arms)==4

def test_prompt_projection_bounds_multibyte_annotations_errors_and_keeps_raw():
    m,ctx,_=fixture();item=experiment(m,ctx);resolve(m,ctx,item)
    record=copy.deepcopy(m.evidence[-1]);record['hypotheses']={'HYPOTHESIS_A':'机制🧬'*20000,'HYPOTHESIS_B':'alternative\n' *10000,'FALSIFIER':'failure '*10000};record['errors']=['timeout🧬'*20000]*4
    m.evidence=[copy.deepcopy(record) for _ in range(12)];original=json.dumps(m.state_dict(),ensure_ascii=False,sort_keys=True)
    wire=m._memory();projection=json.loads(wire)
    assert len(wire)<=24000 and len(wire.encode('utf-8'))<=24000
    assert 0<projection['included_records']<8 and projection['omitted_older_records']==12-projection['included_records']
    assert json.dumps(m.state_dict(),ensure_ascii=False,sort_keys=True)==original
    for row in projection['records']:
        for key in ['effects','anchor_score','states','context_sha256','evaluation_fingerprint','anchor_source_sha256']:
            assert row[key]==record[key]
        assert '[TRUNCATED IN PROMPT' in row['hypotheses']['HYPOTHESIS_A']
        metadata=row['prompt_text_truncations']['hypotheses.HYPOTHESIS_A']
        assert metadata['original_chars']==len(record['hypotheses']['HYPOTHESIS_A']) and len(metadata['sha256'])==64
        assert row['prompt_text_truncations']['errors_serialized_json']['original_utf8_bytes']>2048

def test_short_normal_multiline_memory_is_unchanged():
    m,ctx,_=fixture();item=experiment(m,ctx);resolve(m,ctx,item)
    raw=copy.deepcopy(m.evidence[-1]);projection=json.loads(m._memory());row=projection['records'][0]
    assert row['hypotheses']==raw['hypotheses'] and 'second line preserved' in row['hypotheses']['HYPOTHESIS_A']
    assert row['errors']==raw['errors'] and 'prompt_text_truncations' not in row
    assert projection['omitted_older_records']==0 and m.evidence[-1]==raw

def test_prompt_projection_retains_newest_complete_numeric_records():
    m,ctx,_=fixture();item=experiment(m,ctx);resolve(m,ctx,item);raw=m.evidence[-1]
    m.evidence=[]
    for i in range(8):
        e=copy.deepcopy(raw);e['anchor_id']=f'anchor-{i}';e['hypotheses']={'HYPOTHESIS_A':'x'*10000,'HYPOTHESIS_B':'y'*10000};e['errors']=['z'*10000]*4;m.evidence.append(e)
    p=json.loads(m._memory());n=p['included_records'];assert 0<n<8
    assert [e['anchor_id'] for e in p['records']]==[f'anchor-{i}' for i in range(8-n,8)]
    for e in p['records']:assert e['effects']==raw['effects'] and e['states']==raw['states']

def test_no_observations_adds_no_prompt_memory():
    m,_,_=fixture();assert m._memory()==''
