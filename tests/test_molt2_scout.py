"""Separate-experimenter integration; all model calls mocked, no task evaluations."""
import asyncio,copy,json
from types import SimpleNamespace
from pathlib import Path
import pytest
from test_molt2 import make,run,measured,PY_TEMPLATE
from test_molt2_decision import native_prompt
from pantheon.evolution.methods.molt import Molt,SPROUT
from pantheon.evolution.methods.molt2_scout import Molt2Scout
from pantheon.evolution.methods.molt2_discovery import audited_pair
from pantheon.evolution.core.genome import CodeGenome
from pantheon.evolution.core.individual import Individual
from pantheon.evolution.core.work import Measurement

def fixture(**kwargs):
 _,ctx,seed=make();m=Molt2Scout(num_chains=2,k_candidates=2,num_inspirations=2,selector='rpucg',**kwargs);run(m.start(ctx,[seed]));m._usage_cost=lambda:0.;m.since=0
 m.interventions=[dict(n=0,at=0,children=[dict(direction='i0/d0',type='integration',child=seed.id,score=.6)],directions=[])]
 return m,ctx,seed

def question(seed):return {'anchor_id':seed.id,'mechanism':'component X','decision_to_inform':'keep X or remove it','predicted_contrast':'ON improves public score','falsifier':'ON lower than OFF','expected_cost':{'candidate_evaluations':2,'template_model_calls':1}}
def answer(seed):return json.dumps({'experiment':question(seed)})
def backend(m,reply):
 calls=[]
 async def fake(prompt):calls.append(prompt);return reply
 m._experiment_llm=fake;return calls

def test_native_planner_prompt_is_passed_through_byte_identically(monkeypatch):
 m,ctx,seed=fixture();prompt=native_prompt(m,ctx);seen=[]
 async def fake(self,text):seen.append(text);return 'native response'
 monkeypatch.setattr(Molt,'_llm',fake)
 assert run(m._llm(prompt))=='native response' and seen==[prompt]
 assert 'evidence_request' not in seen[0] and 'OPTIONAL MEASUREMENT' not in seen[0]

def test_native_plan_does_not_harvest_unsolicited_questions(monkeypatch):
 m,ctx,seed=fixture()
 async def fake(self,context):self.pending=[{'evidence_request':question(seed)}]
 monkeypatch.setattr(Molt,'_plan',fake);run(m._plan(ctx));assert not m.requests and not m.scout_audit

def test_ask_separate_question_produces_owned_probe_and_charges_same_slots():
 m,ctx,seed=fixture();calls=backend(m,answer(seed));items=run(m.ask(ctx,2));assert len(calls)==1 and len(items)==2
 probe=next(i for i in items if i.meta.get('molt_discovery_probe'));p=m.probes[probe.batch_id]
 assert p['owner']['native_direction_id']=='i0/d0' and p['native_child_id']==seed.id
 assert m.scout_questions==1 and m.probe_calls==1 and m.slots.probe==2 and m.slots.native==3
 assert '# Component experiment decision' in calls[0] and m.scout_audit[-1]['status']=='queued'

def test_question_and_templates_share_four_calls_not_four_plus_questions():
 m,ctx,seed=fixture();calls=backend(m,json.dumps({'experiment':None,'reason':'no live decision'}))
 for n in range(2):
  if n:m.interventions.append(dict(n=n,at=1,children=[dict(direction='i1/d0',type='integration',child=seed.id)],directions=[]))
  anchor,owner=m._opportunity(ctx);assert not run(m._question(ctx,anchor,owner));assert m.reserve_probe_call() # one matching template reservation
 assert m.probe_calls==4 and m.scout_questions==2 and len(calls)==2
 assert m._opportunity(ctx) is None and not m.reserve_probe_call()

@pytest.mark.parametrize('reply',['[1]','{"directions":[]}','{"experiment":4}','{"experiment":{"anchor_id":"invented"}}'])
def test_malformed_no_retry_no_fabricated_measurement(reply):
 m,ctx,seed=fixture();calls=backend(m,reply);anchor,owner=m._opportunity(ctx)
 assert not run(m._question(ctx,anchor,owner));assert len(calls)==1 and not m.requests and not m.evidence
 assert m.scout_audit[-1]['status']=='malformed' and m.probe_calls==1

def test_invalid_cost_boolean_rejected():
 m,ctx,seed=fixture();q=question(seed);q['expected_cost']['template_model_calls']=True;backend(m,json.dumps({'experiment':q}));a,o=m._opportunity(ctx)
 assert not run(m._question(ctx,a,o)) and not m.requests

def test_no_question_for_unexecuted_direction_or_exhausted_eval_budget():
 m,ctx,seed=fixture();m.interventions[0]['children'][0]['child']='missing';assert m._opportunity(ctx) is None
 m,ctx,seed=fixture();m.slots.native=79;assert m._opportunity(ctx) is None

def test_native_planning_pending_and_open_directions_have_priority():
 m,ctx,seed=fixture();m.since=m.stall_evals;assert m._native_planning_priority() and m._opportunity(ctx) is None
 m.since=0;m.pending=[{'id':'pending'}];assert m._opportunity(ctx) is None
 m.pending=[];m.open_dirs={'open':{}};assert m._opportunity(ctx) is None

def test_base_question_allowed_during_other_probation_when_native_plan_blocked():
 m,ctx,seed=fixture();m.since=m.stall_evals
 for _ in range(m.max_sprouts):
  c=m._new_chain();m.sprouts[c]={'fate':'probation'}
 assert not m._native_planning_priority() and m._opportunity(ctx)[0].id==seed.id
 m.chains[-1].append(seed.id);assert m._opportunity(ctx) is None

def test_old_family_below_frontier_not_worth_paid_question():
 m,ctx,seed=fixture();child=ctx.store.add(Individual(kind='code',genome=CodeGenome(files={'solution.py':'# redesign'}),parent_ids=[seed.id]));child.measurements.append(Measurement(individual_id=child.id,metrics={'combined_score':1.,'validity':1},fidelity='full'));c=m._new_chain();m.chains[c].append(child.id);m.sprouts[c]={'fate':'probation'}
 assert m._opportunity(ctx) is None

def test_anchor_source_changed_during_question_rejected_without_probe():
 m,ctx,seed=fixture();a,o=m._opportunity(ctx)
 async def fake(prompt):seed.genome.files['solution.py']+='# changed\n';return answer(seed)
 m._experiment_llm=fake;assert not run(m._question(ctx,a,o));assert not m.requests and m.scout_audit[-1]['status']=='stale_context'

def test_partial_validity_arm_is_inconclusive_and_does_not_age_native_search():
 m,ctx,seed=fixture();backend(m,answer(seed));a,o=m._opportunity(ctx);assert run(m._question(ctx,a,o));item=m._probe_item(ctx);before=m.native_callbacks
 off,on,_=audited_pair({'solution.py':PY_TEMPLATE},'solution.py')
 for files,valid in [(off,1.),(on,.5)]:
  ind,measurement=measured(ctx,item,files,score=.6,validity=valid);run(m.on_measured(ctx,ind,measurement))
 assert m.evidence[-1]['status']=='inconclusive' and m.evidence[-1]['delta'] is None and m.native_callbacks==before

def test_restart_keeps_call_budget_ownership_and_no_repeat_question():
 m,ctx,seed=fixture();backend(m,'{"experiment":null}');a,o=m._opportunity(ctx);run(m._question(ctx,a,o));state=json.loads(json.dumps(m.state_dict()));other,_,_=fixture();other.load_state_dict(state)
 assert other.scout_questions==1 and other.probe_calls==1 and other.scouted_interventions=={0}
 assert other._opportunity(ctx) is None and other.scout_audit==m.scout_audit

def test_cancelled_question_is_counted_and_archived():
 m,ctx,seed=fixture();a,o=m._opportunity(ctx)
 async def fake(prompt):raise asyncio.CancelledError()
 m._experiment_llm=fake
 with pytest.raises(asyncio.CancelledError):run(m._question(ctx,a,o))
 assert m.scout_questions==1 and m.probe_calls==1 and m.scout_audit[-1]['status']=='cancelled'

def test_question_sdk_uses_same_model_provider_and_ordinary_reasoning(monkeypatch):
 import openai,pantheon.utils.llm_providers as providers
 from pantheon.evolution.variators import usage
 m,ctx,seed=fixture();m.providers=['Pinned'];m.max_tokens=32768;seen=[];responses=[]
 async def create(**kwargs):seen.append(kwargs);return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"experiment":null}'))])
 def client(**kwargs):return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
 monkeypatch.setattr(openai,'AsyncOpenAI',client);monkeypatch.setattr(providers,'detect_provider',lambda *args:SimpleNamespace(api_key='test',base_url='https://example.invalid',model_name='same-model'));monkeypatch.setattr(usage,'add_response',lambda response:responses.append(response))
 assert run(m._experiment_llm('question'))=='{"experiment":null}'
 assert seen[0]['model']=='same-model' and seen[0]['extra_body']['provider']=={'order':['Pinned'],'allow_fallbacks':False}
 assert seen[0]['extra_body']['reasoning']=={'enabled':False} and seen[0]['max_tokens']==4096 and len(responses)==1

def test_paid_question_model_failure_is_logged_once_without_retry():
 m,ctx,seed=fixture();a,o=m._opportunity(ctx);calls=[]
 async def fake(prompt):calls.append(prompt);raise RuntimeError('synthetic provider failure')
 m._experiment_llm=fake;assert not run(m._question(ctx,a,o));assert len(calls)==1 and m.probe_calls==1 and m.scout_audit[-1]['status']=='model_error'

def test_controller_errors_are_not_misreported_as_model_failures(monkeypatch):
 m,ctx,seed=fixture();backend(m,answer(seed));a,o=m._opportunity(ctx)
 def broken(*args):raise TypeError('synthetic controller bug')
 monkeypatch.setattr(m,'queue_evidence_request',broken)
 with pytest.raises(TypeError,match='controller bug'):run(m._question(ctx,a,o))
 assert m.scout_audit[-1]['status']!='model_error'

def test_probe_decision_obsolete_after_new_frontier_is_rejected_without_eval_slots():
 m,ctx,seed=fixture();backend(m,answer(seed));a,o=m._opportunity(ctx);assert run(m._question(ctx,a,o))
 child=ctx.store.add(Individual(kind='code',genome=CodeGenome(files={'solution.py':'# new frontier'}),parent_ids=[seed.id]));child.measurements.append(Measurement(individual_id=child.id,metrics={'combined_score':1.,'validity':1},fidelity='full'))
 assert m._probe_item(ctx) is None and m.slots.probe==0 and not m.requests
 assert m.rejected_requests[-1]['reason']=='anchor_no_longer_established_valid_or_frontier_relevant'

def test_question_contains_persisted_native_hypothesis_not_only_owner_id():
 m,ctx,seed=fixture();m.interventions[0]['directions']=[{'id':'i0/d0','type':'integration','task':'retain neighborhood component A'}];a,o=m._opportunity(ctx);prompt=m._question_prompt(ctx,a,o)
 assert 'retain neighborhood component A' in prompt and 'Child first fully valid public score: 0.6' in prompt
