"""Offline native Molt + paired-instrument integration tests: no API/task access."""
import asyncio,copy,json,hashlib
from pathlib import Path
import pytest
from test_molt2 import make,run,measured,PY_TEMPLATE
from pantheon.evolution.methods.molt2_discovery import Molt2Discovery,MoltDiscoveryVariator,CandidateSlots,audited_pair
from pantheon.evolution.methods.molt import Molt,MoltVariator,SPROUT
from pantheon.evolution.core.work import Produced,Measurement,Failure
from pantheon.evolution.core.genome import CodeGenome

def fixture(**kw):
    _,ctx,seed=make();m=Molt2Discovery(num_chains=2,k_candidates=2,num_inspirations=2,selector='rpucg',**kw);run(m.start(ctx,[seed]));return m,ctx,seed

def request(m,ctx,seed):
    m.queue_evidence_request(ctx,dict(anchor_id=seed.id,mechanism='Different representation-local choice',decision_to_inform='whether to retain this component',predicted_contrast='ON improves fixed development objective',falsifier='ON fails to improve',expected_cost='two evaluations'),{'planning_call_id':0})

def ready(m):
    # Exact actual Molt intervention schema: _plan creates children[],
    # _close_direction appends a scored direction child to that list.
    m.interventions=[dict(n=0,children=[dict(direction='i0/d0',child='native-child',score=.6)],directions=[])]

def probe(m,ctx,seed):
    ready(m);request(m,ctx,seed);return m._probe_item(ctx)

def resolve(m,ctx,item,control=.6,treatment=.7,reverse=True):
    off,on,_=audited_pair({'solution.py':PY_TEMPLATE},'solution.py');pairs=[(off,control),(on,treatment)]
    for files,value in (reversed(pairs) if reverse else pairs):
        ind,measurement=measured(ctx,item,files,score=value);run(m.on_measured(ctx,ind,measurement))

# These helpers exercise public work items/registries without invoking paid planners.
def test_source_contrast_reverse_callbacks_do_not_age_native_clocks():
    m,ctx,seed=fixture();item=probe(m,ctx,seed);before=(m.attempts,m.since,copy.deepcopy(m.turns))
    resolve(m,ctx,item);e=m.evidence[-1]
    assert e['delta']==pytest.approx(.1) and e['status']=='positive_observed_pair'
    assert e['anchor_reproduced'] and len(e['context_sha256'])==64
    assert m.attempts==before[0] and m.turns==before[2] and not m.probes
    assert all(len(x['source_sha256'])==64 for x in e['states'].values())
    assert m.anchor==pytest.approx(.7)

def test_missing_score_and_failed_delivery_never_zero_imputed():
    m,ctx,seed=fixture();item=probe(m,ctx,seed);resolve(m,ctx,item,treatment=None)
    assert m.evidence[-1]['delta'] is None and m.evidence[-1]['status']=='inconclusive'

def test_duplicate_callback_and_evaluate_failure_keep_code():
    m,ctx,seed=fixture();item=probe(m,ctx,seed);off,on,_=audited_pair({'solution.py':PY_TEMPLATE},'solution.py')
    ind,measurement=measured(ctx,item,off);run(m.on_measured(ctx,ind,measurement));run(m.on_measured(ctx,ind,measurement))
    assert m.probes[item.batch_id]['resolved']==1
    failed,mf=measured(ctx,item,on,score=None,ok=False,artifacts={'evaluation_error':'timeout'})
    run(m.on_failed(ctx,Failure(item_id=item.id,batch_id=item.batch_id,stage='evaluate',reason='timeout',detail={'individual_id':failed.id})))
    assert m.evidence[-1]['delta'] is None and m.evidence[-1]['states']['treatment']['score'] is None
    assert m.evidence[-1]['measurements']['treatment']['artifacts']['evaluation_error']=='timeout'

def test_generation_shortfall_closes_exactly_once():
    m,ctx,seed=fixture();item=probe(m,ctx,seed)
    for _ in range(2):run(m.on_failed(ctx,Failure(item_id=item.id,batch_id=item.batch_id,stage='generate',reason='empty')))
    assert len(m.evidence)==1 and not m.probes and m.evidence[-1]['delta'] is None

def test_pending_direction_and_probation_prevent_probe():
    m,ctx,seed=fixture();ready(m);request(m,ctx,seed)
    m.pending=[{'id':'pending-native'}];assert not m._can_probe();m.pending=[]
    c=m._new_chain();m.role[c]=SPROUT;assert not m._can_probe()

def test_stale_anchor_context_rejected():
    m,ctx,seed=fixture();ready(m);request(m,ctx,seed);m.fingerprint='changed-protocol'
    assert m._probe_item(ctx) is None and m.rejected_requests[-1]['reason']=='stale_anchor_or_context'

def test_context_memory_not_transferred_to_unrelated_parents():
    m,ctx,seed=fixture();item=probe(m,ctx,seed);resolve(m,ctx,item)
    assert 'positive_observed_pair' in m._memory(ctx,[seed.id])
    assert m._memory(ctx,['unrelated-id'])==''

def test_reservations_atomic_capacity_and_eight_probe_ceiling():
    ledger=CandidateSlots(80,8)
    for _ in range(4):assert ledger.reserve(2,probe=True)==2
    assert ledger.reserve(2,probe=True)==0
    assert ledger.reserve(100)==72 and ledger.remaining()==0

def test_resume_partial_probe_archives_state_without_retry():
    m,ctx,seed=fixture();item=probe(m,ctx,seed);off,_,_=audited_pair({'solution.py':PY_TEMPLATE},'solution.py')
    ind,measurement=measured(ctx,item,off);run(m.on_measured(ctx,ind,measurement))
    state=json.loads(json.dumps(m.state_dict()));new,_,_=fixture();new.load_state_dict(state);new.reconcile(ctx)
    assert not new.probes and new.slots.probe==2
    assert new.evidence[-1]['status']=='interrupted_unresolved' and new.evidence[-1]['delta'] is None
    assert new.evidence[-1]['executable_states']['control']==off

@pytest.mark.parametrize('reply',['[]','[1]','"string"','3','{"directions":3}','{"directions":[null,3,{"evidence_request":[]}]}'])
def test_malformed_planner_evidence_never_crashes(monkeypatch,reply):
    m,ctx,_=fixture();m._planning_ctx=ctx
    async def fake(self,prompt):return reply
    monkeypatch.setattr(Molt,'_llm',fake)
    result=run(m._llm('# Dynamic branch intervention portfolio\n'))
    assert isinstance(json.loads(result),dict) and not m.requests


def test_actual_driver_native_cohort_probation_and_pair_within80(monkeypatch):
    from pantheon.evolution.methods.simpletes import UpstreamCompletionVariator
    from pantheon.evolution.core.loop import evolve
    from pantheon.evolution.core.method import Budget
    from pantheon.evolution.core.individual import Individual
    m=Molt2Discovery(num_chains=2,k_candidates=2,num_inspirations=2,selector='rpucg',seed=0)
    seedfiles={'solution.py':'def solve(x): return x\n'};wire=[];natives=[];probes=[];counter=0
    async def planner(self,prompt):
        if prompt.startswith('You are removing'):
            pool=json.loads(prompt.split('Candidate directions:\n\n',1)[1]);return json.dumps({'kept_direction_ids':[next(iter(pool))]})
        if self.interventions:return json.dumps({'directions':[]})
        anchor=next(iter(self._planning_ctx.store)).id
        return json.dumps({'directions':[{'type':'redesign','target_branch_id':anchor,'task':'New independent representation','mechanism_hypothesis':'Alternative decomposition','donor_branch_ids':[],'evidence_request':{'anchor_id':anchor,'mechanism':'A local component after architecture exploration','decision_to_inform':'retain component','predicted_contrast':'ON improves objective','falsifier':'ON no better','expected_cost':'2evaluations'}}]})
    monkeypatch.setattr(Molt,'_llm',planner)
    async def create(self,ctx,item):
        nonlocal counter
        wire.append((item.meta.get('molt_discovery_probe',False),item.k))
        if item.meta.get('molt_discovery_probe'):return [Produced(item_id=item.id,genome=CodeGenome(files={'solution.py':PY_TEMPLATE}))]
        out=[]
        for _ in range(item.k):
            counter+=1;files={'solution.py':f'# native{counter}\ndef solve(x): return x\n'}
            out.append(Produced(item_id=item.id,genome=CodeGenome(files=files),meta=dict(item.meta)))
        return out
    monkeypatch.setattr(UpstreamCompletionVariator,'create',create)
    class Evaluator:
        async def measure(self,ctx,ind,fidelity):
            code=ind.genome.files['solution.py'];value=.6
            if 'MOLT2_TREATMENT' in code:
                probes.append(ind.id);value=.7 if switch_value(code,'solution.py') else .6
            else:natives.append(ind.id);value=.61 if ind.parent_ids else .6
            return Measurement(individual_id=ind.id,fidelity='full',metrics={'combined_score':value,'validity':1})
    from pantheon.evolution.methods.molt2 import switch_value
    v=MoltDiscoveryVariator.__new__(MoltDiscoveryVariator);v.target_file='solution.py';v.discovery_owner=m
    result=run(evolve(method=m,variator=v,evaluators={'code':Evaluator()},seeds=[CodeGenome(files=seedfiles)],objective='Fixed development toy objective',budget=Budget(max_items=100),concurrency=2))
    assert m.interventions and any(i['children'] for i in m.interventions)
    assert m.sprouts and m.evidence and len(probes)>=2
    assert m.slots.native>=72 and m.slots.probe<=8 and m.slots.native+m.slots.probe==80
    assert len(natives)+len(probes)==80 and not m.probes
    assert all(k==1 for diagnostic,k in wire if diagnostic)
    assert any(e['delta'] is not None for e in m.evidence)

def test_native_direction_prompt_identical_without_memory():
    from pantheon.evolution.variators.completion import EvolveBlock
    m,ctx,seed=fixture();d={'id':'i0/d0','intervention':0,'type':'redesign','target_branch_id':seed.id,'task':'Independent architecture','mechanism_hypothesis':'Different representation','donor_branch_ids':[],'frontier':.6}
    item=m._direction_item(ctx,d);eb=EvolveBlock('# EVOLVE-BLOCK-START\ndef solve(x): return x\n# EVOLVE-BLOCK-END\n')
    original=MoltVariator.__new__(MoltVariator);original.score_key='combined_score'
    upgraded=MoltDiscoveryVariator.__new__(MoltDiscoveryVariator);upgraded.score_key='combined_score'
    p=original.build_block_prompt(ctx,item,'solution.py',eb)
    assert upgraded.build_block_prompt(ctx,item,'solution.py',eb)==p
    assert 'I/O REFERENCE' in p and 'redesign' in p and 'Independent architecture' in p

def test_planner_request_ownership_survives_duplicate_native_directions(monkeypatch):
    m,ctx,seed=fixture();m._planning_ctx=ctx
    r=dict(anchor_id=seed.id,mechanism='component',decision_to_inform='retain?',predicted_contrast='positive',falsifier='notpositive')
    async def fake(self,prompt):return json.dumps({'directions':[{'type':'redesign','evidence_request':r}]})
    monkeypatch.setattr(Molt,'_llm',fake)
    run(m._llm('# Dynamic branch intervention portfolio\n'));run(m._llm('# Dynamic branch intervention portfolio\n'))
    assert len(m.requests)==2
    assert m.requests[0]['owner']['planning_call_id']!=m.requests[1]['owner']['planning_call_id']
    assert m.requests[0]['experiment_id']!=m.requests[1]['experiment_id']

def test_hard_probe_call_cap_and_observed_dollar_gate(monkeypatch):
    from pantheon.evolution.methods.molt2_discovery import PROBE
    m,ctx,_=fixture();v=MoltDiscoveryVariator.__new__(MoltDiscoveryVariator);v.discovery_owner=m;wire=[]
    async def fake(self,prompt,k,**kwargs):wire.append(k);return ['code']
    monkeypatch.setattr(MoltVariator,'_complete',fake)
    async def scenario():
        token=PROBE.set(True)
        try:
            for _ in range(4):assert await v._complete('prompt',1)==['code']
            with pytest.raises(RuntimeError,match='allowance'):await v._complete('prompt',1)
        finally:PROBE.reset(token)
        # Ordinary architecture work remains on native path after diagnostic cap.
        assert await v._complete('native',2)==['code']
    run(scenario());assert wire==[1,1,1,1,2]
    m.probe_calls=0;m.probe_cost_upper_bound=.05;assert not m.reserve_probe_call()
    m.probe_cost_upper_bound=0;monkeypatch.setattr(m,'_usage_cost',lambda:.40);assert not m.reserve_probe_call()

def test_mismatched_templates_have_no_evidence_delta():
    m,ctx,seed=fixture();item=probe(m,ctx,seed);off,on,_=audited_pair({'solution.py':PY_TEMPLATE},'solution.py');on['solution.py']+='\n# unrelated rewrite\n'
    for files in [off,on]:
        ind,measurement=measured(ctx,item,files);run(m.on_measured(ctx,ind,measurement))
    assert m.evidence[-1]['delta'] is None and not m.evidence[-1]['one_switch_source_contrast']

def test_multiline_hypotheses_and_per_case_artifacts_retained():
    m,ctx,seed=fixture();item=probe(m,ctx,seed)
    code=PY_TEMPLATE.replace('MOLT2_HYPOTHESIS: alter the neighborhood representation','MOLT2_HYPOTHESIS: first line\n# second line')
    off,on,_=audited_pair({'solution.py':code},'solution.py')
    for files in [off,on]:
        ind,measurement=measured(ctx,item,files,artifacts={'per_case_scores':[1,2,3]});run(m.on_measured(ctx,ind,measurement))
    e=m.evidence[-1]
    assert e['measurements']['control']['artifacts']['per_case_scores']==[1,2,3]
    assert e['executable_states']['control']==off

def test_bad_request_does_not_remove_valid_native_direction(monkeypatch):
    m,ctx,seed=fixture();d={'type':'exploration','target_branch_id':seed.id,'task':'Native valid direction','donor_branch_ids':[],'evidence_request':{'anchor_id':seed.id,'mechanism':[]}}
    async def fake(self,prompt):return json.dumps({'directions':[d]}) if prompt.startswith('# Dynamic') else '{}'
    monkeypatch.setattr(Molt,'_llm',fake);run(m._plan(ctx))
    assert m.pending and all(x['task']=='Native valid direction' for x in m.pending)
    assert not m.requests

def test_due_native_planning_has_priority_even_with_one_residual_worker(monkeypatch):
    m,ctx,seed=fixture();ready(m);request(m,ctx,seed);m.since=m.stall_evals
    called=[]
    async def native(self,ctx,n):called.append(n);return []
    monkeypatch.setattr(Molt,'ask',native)
    assert not m._can_probe();assert run(m.ask(ctx,1))==[] and called and not m.probes

def test_changed_objective_context_rejects_queued_request():
    m,ctx,seed=fixture();ready(m);request(m,ctx,seed);ctx.objective+=' changed decision context'
    assert m._probe_item(ctx) is None and m.rejected_requests

def test_changed_anchor_bytes_rejects_queued_request():
    m,ctx,seed=fixture();ready(m);request(m,ctx,seed);seed.genome.files['solution.py']+='\n# changed\n'
    assert m._probe_item(ctx) is None and m.rejected_requests

def test_negative_component_memory_stops_at_real_redesign_lineage():
    from pantheon.evolution.core.individual import Individual
    m,ctx,seed=fixture();item=probe(m,ctx,seed);resolve(m,ctx,item,treatment=.5)
    same=ctx.store.add(Individual(genome=CodeGenome(files={'solution.py':'# same representation child\n'}),parent_ids=[seed.id],kind='code'))
    assert 'negative_observed_pair' in m._memory(ctx,[same.id])
    # Real native direction keeps old source as parent despite architecture reset.
    d=dict(id='i0/new',intervention=0,type='redesign',target_branch_id=seed.id,donor_branch_ids=[],task='Different representation',frontier=.6)
    direction=m._direction_item(ctx,d)
    assert direction.parent_ids==[seed.id] and direction.meta['molt_type']=='redesign'
    redesigned=ctx.store.add(Individual(genome=CodeGenome(files={'solution.py':'# different architecture\n'}),parent_ids=direction.parent_ids,meta=direction.meta,kind='code'))
    descendant=ctx.store.add(Individual(genome=CodeGenome(files={'solution.py':'# architecture refinement\n'}),parent_ids=[redesigned.id],kind='code'))
    assert m._memory(ctx,[redesigned.id])=='' and m._memory(ctx,[descendant.id])==''
    # Exact old source remains an inspectable context in a planner global overview.
    assert 'negative_observed_pair' in m._memory(ctx,[seed.id])

def test_known_sprout_root_boundary_survives_missing_genome_metadata():
    from pantheon.evolution.core.individual import Individual
    m,ctx,seed=fixture();item=probe(m,ctx,seed);resolve(m,ctx,item,treatment=.5)
    root=ctx.store.add(Individual(genome=CodeGenome(files={'solution.py':'# redesigned source with dedup metadata\n'}),parent_ids=[seed.id],kind='code'))
    c=m._new_chain();m.chains[c].append(root.id);m.sprouts[c]={'fate':'probation'}
    child=ctx.store.add(Individual(genome=CodeGenome(files={'solution.py':'# child of redesigned root\n'}),parent_ids=[root.id],kind='code'))
    assert m._memory(ctx,[child.id])==''

def test_concurrent_slot_reservations_never_oversubscribe():
    from concurrent.futures import ThreadPoolExecutor
    ledger=CandidateSlots(80,8)
    with ThreadPoolExecutor(max_workers=16) as pool:
        outcomes=list(pool.map(lambda probe:ledger.reserve(2,probe=probe),[True,False]*50))
    assert sum(outcomes)==80 and ledger.native+ledger.probe==80 and ledger.probe<=8 and ledger.native>=72

def test_vanilla_molt_source_is_byte_identical_to_parent_commit():
    import subprocess
    root=Path(__file__).resolve().parents[1];path='pantheon/evolution/methods/molt.py'
    before=subprocess.check_output(['git','show','b6a113a0728c95c497e8865fbbf280e9631d7d04:'+path],cwd=root)
    assert (root/path).read_bytes()==before

def test_resume_keeps_custom_diagnostic_caps_and_rejects_future_schema():
    m,ctx,_=fixture(max_probe_calls=2,probe_usd_threshold=.025,native_usd_headroom=.15)
    state=json.loads(json.dumps(m.state_dict()));new,_,_=fixture();new.load_state_dict(state)
    assert new.max_probe_calls==2 and new.probe_usd_threshold==.025 and new.native_usd_headroom==.15
    state['discovery']['schema']=2
    with pytest.raises(ValueError,match='schema'):new.load_state_dict(state)
