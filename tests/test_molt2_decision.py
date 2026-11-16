"""Decision-contract and native-priority integration; no model/network/task calls."""
import copy,json,subprocess
from pathlib import Path
import pytest
from test_molt2 import make,run,measured,PY_TEMPLATE
from pantheon.evolution.methods.molt import Molt,PLANNER,BASE,ESTABLISHED,SPROUT
from pantheon.evolution.methods.molt2_discovery import MoltDiscoveryVariator,audited_pair
from pantheon.evolution.methods.molt2_decision import Molt2Decision
from pantheon.evolution.methods.decision_prompt import build_decision_planner_prompt
from pantheon.evolution.core.genome import CodeGenome
from pantheon.evolution.core.individual import Individual
from pantheon.evolution.core.work import Produced,Measurement

def fixture(**kw):
    _,ctx,seed=make();m=Molt2Decision(num_chains=2,k_candidates=2,num_inspirations=2,selector='rpucg',**kw);run(m.start(ctx,[seed]));return m,ctx,seed

def question(seed):return dict(anchor_id=seed.id,mechanism='Component choice',decision_to_inform='retain component or not',predicted_contrast='ON improves development score',falsifier='ON not better',expected_cost={'candidate_evaluations':2,'template_model_calls':1})
def ready(m,seed):m.interventions=[dict(n=0,children=[dict(direction='i0/d0',child=seed.id,score=.6)],directions=[])]
def enqueue(m,ctx,seed):return m.queue_evidence_request(ctx,question(seed),{'native_direction_id':'i0/d0','intervention':0,'native_direction_type':'redesign'})
def native_prompt(m,ctx):
    _,_,catalog,attempts,code,tried=m._views(ctx)
    return PLANNER.format(objective=ctx.objective,catalog=catalog,attempts='\n'.join(attempts),code=code,tried=tried,num_directions=m.directions_per_planner,**m._type_slots())

def test_schema_nullable_field_integrated_and_old_optional_appendix_removed():
    m,ctx,seed=fixture();original=native_prompt(m,ctx)
    p=build_decision_planner_prompt(original+'\nOPTIONAL MEASUREMENT INSTRUMENT (not a fourth direction type):\nold optional content',[seed.id])
    assert '"verbalized_probability": 0.0,\n      "evidence_request": null}' in p
    assert 'at least ONE non-null' in p and 'old optional content' not in p and seed.id in p
    assert 'redesign' in p and '## Branch catalog' in p
    with pytest.raises(ValueError,match='contract changed'):build_decision_planner_prompt('not native prompt',[seed.id])

def test_base_anchor_probe_eligible_with_multiple_probation_sprouts():
    m,ctx,seed=fixture();ready(m,seed);assert enqueue(m,ctx,seed)
    for i in range(3):
        c=m._new_chain();m.sprouts[c]={'fate':'probation'}
    assert len(m._active((SPROUT,)))==3 and m._can_probe()
    item=m._probe_item(ctx);assert item and item.k==2
    assert seed.id==m.probes[item.batch_id]['context']['anchor_id']

def test_probation_anchor_rejected_even_though_other_anchors_allowed():
    m,ctx,seed=fixture();ready(m,seed)
    child=ctx.store.add(Individual(genome=CodeGenome(files={'solution.py':'# sprout\n'}),kind='code',parent_ids=[seed.id]))
    child.measurements.append(Measurement(individual_id=child.id,fidelity='full',metrics={'combined_score':.61,'validity':1}))
    c=m._new_chain();m.chains[c].append(child.id);m.sprouts[c]={'fate':'probation'}
    assert not enqueue(m,ctx,child) and not m.requests
    assert enqueue(m,ctx,seed)

def test_native_planning_priority_when_due_and_allowed():
    m,ctx,seed=fixture();ready(m,seed);enqueue(m,ctx,seed);m.since=m.stall_evals
    c=m._new_chain();m.sprouts[c]={'fate':'probation'}
    assert m._native_planning_priority() and not m._can_probe()
    # Native Molt blocks a new cohort at max_sprouts. It cannot indefinitely block
    # a residual established-anchor instrument just because planning is due.
    for _ in range(3):c=m._new_chain();m.sprouts[c]={'fate':'probation'}
    assert not m._native_planning_priority() and m._can_probe()

@pytest.mark.parametrize('reply',['[1]','"text"','4','{"directions":4}'])
def test_malformed_planning_reply_has_native_safe_empty_direction_set(monkeypatch,reply):
    m,ctx,_=fixture();m._planning_ctx=ctx
    async def fake(self,prompt):return reply
    monkeypatch.setattr(Molt,'_llm',fake)
    assert json.loads(run(m._llm(native_prompt(m,ctx))))['directions']==[]

def test_noncompliance_recorded_not_fabricated_or_retried(monkeypatch):
    m,ctx,seed=fixture();m._planning_ctx=ctx;seen=[]
    async def fake(self,prompt):seen.append(prompt);return json.dumps({'directions':[{'type':'redesign','target_branch_id':seed.id}]})
    monkeypatch.setattr(Molt,'_llm',fake);out=json.loads(run(m._llm(native_prompt(m,ctx))))
    assert 'evidence_request' not in out['directions'][0] and len(seen)==1
    assert m.planner_contract_audit[-1]['status']=='contract_not_met'


def test_discarded_direction_request_and_context_boundaries_still_hold(monkeypatch):
    m,ctx,seed=fixture();req=question(seed)
    async def fake(self,prompt):
        if prompt.startswith('You are removing'):
            pool=json.loads(prompt.split('Candidate directions:\n\n')[1]);return json.dumps({'kept_direction_ids':[next(iter(pool))]})
        return json.dumps({'directions':[{'type':'redesign','target_branch_id':seed.id,'evidence_request':None},{'type':'exploration','target_branch_id':seed.id,'evidence_request':req},{'type':'redesign','target_branch_id':'invalid','evidence_request':req}]})
    monkeypatch.setattr(Molt,'_llm',fake);run(m._plan(ctx));assert m.pending and not m.requests


def test_actual_driver_pair_completes_while_multiple_native_sprouts_on_probation(monkeypatch):
    from pantheon.evolution.methods.simpletes import UpstreamCompletionVariator
    from pantheon.evolution.methods.molt2 import switch_value
    from pantheon.evolution.core.loop import evolve
    from pantheon.evolution.core.method import Budget
    m=Molt2Decision(num_chains=2,k_candidates=2,num_inspirations=2,selector='rpucg',seed=0)
    generated=0;native_calls=[];probe_calls=[];observed_probation=[]
    async def planner(self,prompt):
        if prompt.startswith('You are removing'):
            pool=json.loads(prompt.split('Candidate directions:\n\n')[1]);return json.dumps({'kept_direction_ids':[k for k,v in pool.items() if v['type']=='redesign']})
        if self.interventions:return json.dumps({'directions':[]})
        anchor=next(iter(self._planning_ctx.store));req=question(anchor)
        return json.dumps({'directions':[{'type':'redesign','target_branch_id':anchor.id,'task':'Distinct architecture','mechanism_hypothesis':'Different decomposition','evidence_request':req},{'type':'exploration','target_branch_id':anchor.id,'task':'Other independent decision','evidence_request':None}]})
    async def create(self,ctx,item):
        nonlocal generated
        if item.meta.get('molt_discovery_probe'):
            probe_calls.append(item.k);return [Produced(item_id=item.id,genome=CodeGenome(files={'solution.py':PY_TEMPLATE}))]
        out=[]
        for _ in range(item.k):
            generated+=1;out.append(Produced(item_id=item.id,genome=CodeGenome(files={'solution.py':f'# native{generated}\ndef solve(x):return x\n'}),meta=dict(item.meta)))
        return out
    class Evaluator:
        async def measure(self,ctx,ind,fidelity):
            flag=switch_value(ind.genome.files['solution.py'],'solution.py')
            if flag is not None:observed_probation.append(len(m._active((SPROUT,))));value=.6 if flag==0 else .7
            else:native_calls.append(ind.id);value=.6 if not ind.parent_ids else .61
            return Measurement(individual_id=ind.id,fidelity='full',metrics={'combined_score':value,'validity':1})
    monkeypatch.setattr(Molt,'_llm',planner);monkeypatch.setattr(UpstreamCompletionVariator,'create',create)
    v=MoltDiscoveryVariator.__new__(MoltDiscoveryVariator);v.target_file='solution.py';v.discovery_owner=m
    run(evolve(m,v,{'code':Evaluator()},[CodeGenome(files={'solution.py':'def solve(x):return x\n'})],objective='Fixed public toy objective',budget=Budget(max_items=100),concurrency=2))
    assert m.evidence and max(observed_probation)>=2 and probe_calls and all(k==1 for k in probe_calls)
    assert any(e['delta'] is not None for e in m.evidence)
    assert m.slots.native>=72 and m.slots.probe<=8 and m.slots.native+m.slots.probe==80
    assert len(native_calls)+len(observed_probation)==80
    assert all(x['status']=='compliant' for x in m.planner_contract_audit if x.get('directions'))
    for e in m.evidence:
        assert e['owner']['native_direction_id'] in [x['id'] for i in m.interventions for x in i['directions']]


def test_parent_native_and_discovery_source_byte_identical():
    root=Path(__file__).resolve().parents[1]
    for file in ['molt.py','simpletes.py','molt2_discovery.py','molt2_factorial.py','molt2.py']:
        path='pantheon/evolution/methods/'+file
        assert (root/path).read_bytes()==subprocess.check_output(['git','show','961d72554ed80a5e82fc9ef5e11671cd0b14776d:'+path],cwd=root)

def test_decision_negative_memory_keeps_redesign_boundary_and_native_clocks():
    m,ctx,seed=fixture();ready(m,seed);enqueue(m,ctx,seed);item=m._probe_item(ctx)
    before=(m.attempts,copy.deepcopy(m.turns));off,on,_=audited_pair({'solution.py':PY_TEMPLATE},'solution.py')
    for files,score in [(on,.5),(off,.6)]:
        ind,measurement=measured(ctx,item,files,score=score);run(m.on_measured(ctx,ind,measurement))
    assert m.attempts==before[0] and m.turns==before[1]
    child=ctx.store.add(Individual(genome=CodeGenome(files={'solution.py':'# same-family\n'}),kind='code',parent_ids=[seed.id]))
    new=ctx.store.add(Individual(genome=CodeGenome(files={'solution.py':'# new-family\n'}),kind='code',parent_ids=[seed.id],meta={'molt_type':'redesign'}))
    descendant=ctx.store.add(Individual(genome=CodeGenome(files={'solution.py':'# new-family-refined\n'}),kind='code',parent_ids=[new.id]))
    assert 'negative_observed_pair' in m._memory(ctx,[child.id]) and m._memory(ctx,[descendant.id])==''


def test_decision_checkpoint_preserves_contract_audit_and_partial_arm_without_retry():
    m,ctx,seed=fixture();ready(m,seed);enqueue(m,ctx,seed);item=m._probe_item(ctx);off,_,_=audited_pair({'solution.py':PY_TEMPLATE},'solution.py')
    ind,measurement=measured(ctx,item,off);run(m.on_measured(ctx,ind,measurement));m.planner_contract_audit=[{'status':'compliant','nonnull_requests':1}]
    state=json.loads(json.dumps(m.state_dict()));new,_,_=fixture();new.load_state_dict(state);new.reconcile(ctx)
    assert new.planner_contract_audit==m.planner_contract_audit and new.slots.probe==2 and not new.probes
    assert new.evidence[-1]['status']=='interrupted_unresolved' and new.evidence[-1]['delta'] is None
    assert new.evidence[-1]['executable_states']['control']==off

def test_guarded_real_preflight_receipts_bind_current_prompt_helper():
    import hashlib
    root=Path(__file__).resolve().parents[1];receipt=json.loads((root/'tests/decision-contract-preflight.json').read_text())
    digest=hashlib.sha256((root/'pantheon/evolution/methods/decision_prompt.py').read_bytes()).hexdigest()
    assert receipt['real_api_calls']==2 and len(receipt['receipts'])==2
    assert all(r['helper_sha256']==digest and r['compliance']['contract_pass'] and r['actual_provider']=='Wafer' for r in receipt['receipts'])
    assert receipt['reported_total_usd']==pytest.approx(sum(r['reported_cost_usd'] for r in receipt['receipts']))
