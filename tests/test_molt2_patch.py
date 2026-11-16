"""Raw-control/edit source integrity, no paid calls or task evaluations."""
import copy,json
import pytest
from test_molt2 import make,run,measured
from test_molt2_scout import backend
from test_molt2_scout_lineage import direction_child
from pantheon.evolution.methods.molt import Molt
from pantheon.evolution.methods.molt2_patch import Molt2Patch,MoltPatchVariator,exact_edit
from pantheon.evolution.methods.molt2_discovery import MoltDiscoveryVariator
from pantheon.evolution.core.work import Failure,Measurement

def fixture():
 _,ctx,seed=make();m=Molt2Patch(num_chains=2,k_candidates=2,num_inspirations=2,selector='rpucg',seed=0);run(m.start(ctx,[seed]));m._usage_cost=lambda:0.;m.since=0
 m.interventions=[dict(n=0,at=0,children=[dict(direction='i0/d0',type='integration',child=seed.id,score=.6)],directions=[])]
 return m,ctx,seed

def edit(seed):return {'path':'solution.py','before':'return x','after':'return x + 1'}
def answer(seed,patch=None):return json.dumps({'experiment':{'anchor_id':seed.id,'mechanism':'exact return expression alternative','decision_to_inform':'retain this concrete implementation edit','predicted_contrast':'treatment improves public score','falsifier':'treatment does not improve','expected_cost':{'candidate_evaluations':2,'template_model_calls':0},'source_edit':patch if patch is not None else edit(seed)}})
def probe():
 m,ctx,seed=fixture();anchor,owner=m._opportunity(ctx);calls=backend(m,answer(anchor));assert run(m._question(ctx,anchor,owner));item=m._probe_item(ctx);assert item is not None
 return m,ctx,seed,item,calls

def resolve(m,ctx,item,control=.6,treatment=.7,validity=1):
 p=m.probes[item.batch_id]
 for arm,score in [('control',control),('treatment',treatment)]:
  ind,measurement=measured(ctx,item,p['expected_files'][arm],score=score,validity=validity if arm=='treatment' else 1,ok=score is not None)
  run(m.on_measured(ctx,ind,measurement))

def test_multifile_control_is_byte_identical_and_only_one_region_changes():
 files={'solution.cpp':'// header\nint f(){return 1;}\n','data.txt':'unchanged\n'};original=copy.deepcopy(files)
 control,treatment=exact_edit(files,{'path':'solution.cpp','before':'return 1','after':'return 2'})
 assert files==original and control==original and control is not files
 assert treatment['data.txt']==files['data.txt'] and treatment['solution.cpp']==files['solution.cpp'].replace('return 1','return 2')

@pytest.mark.parametrize('patch',[None,{}, {'path':'new.cpp','before':'return x','after':'return y'}, {'path':'solution.py','before':'','after':'x'}, {'path':'solution.py','before':'return x','after':'return x'}, {'path':'solution.py','before':'missing','after':'x'},{'path':'solution.py','before':'a'*4097,'after':'b'},{'path':'solution.py','before':'return x','after':'b'*4097},{'path':'solution.py','before':'return x','after':'return y','extra':'other edit'}])
def test_missing_ambiguous_noop_or_oversized_edits_are_rejected(patch):
 with pytest.raises(ValueError):exact_edit({'solution.py':'return x\n'},patch)

def test_duplicate_context_rejected_no_guess_at_match_location():
 with pytest.raises(ValueError):exact_edit({'solution.py':'return x\nreturn x\n'},edit(None))

def test_one_question_no_template_call_and_two_candidate_slots(monkeypatch):
 m,ctx,seed,item,calls=probe();v=m.default_variator(model='unused',target_file='solution.py')
 async def forbidden(*a,**k):raise AssertionError('No template SDK call allowed')
 monkeypatch.setattr(MoltDiscoveryVariator,'create',forbidden)
 produced=run(v.create(ctx,item));assert len(produced)==2 and len(calls)==1 and m.probe_calls==1 and m.slots.probe==2
 assert produced[0].genome.files==seed.genome.files
 assert produced[1].genome.files==exact_edit(seed.genome.files,edit(seed))[1]
 assert '"template_model_calls":0' in calls[0] and '"template_model_calls":1' not in calls[0]

def test_normal_work_still_uses_native_variator(monkeypatch):
 m,ctx,seed=fixture();from pantheon.evolution.core.work import Create
 item=Create(kind='code',parent_ids=[seed.id]);v=m.default_variator(model='unused');seen=[]
 async def native(self,context,work):seen.append(work.id);return ['native']
 monkeypatch.setattr(MoltDiscoveryVariator,'create',native);assert run(v.create(ctx,item))==['native'] and seen==[item.id]

def test_positive_edit_records_exact_control_and_reuses_only_treatment():
 m,ctx,seed,item,_=probe();turns=m.native_callbacks;resolve(m,ctx,item);row=m.evidence[-1]
 assert row['status']=='positive_observed_pair' and row['raw_anchor_control_exact'] and row['exact_source_edit']
 assert row['executable_states']['control']==seed.genome.files and row['one_switch_source_contrast'] is False
 assert row['states']['control']['individual_id']==seed.id and m.chains[0]==[seed.id,row['states']['treatment']['individual_id']]
 assert m.native_callbacks==turns and 'EXACT_SOURCE_EDIT' in m._memory(ctx,[seed.id])

def test_negative_edit_not_admitted_and_not_universal_claim():
 m,ctx,seed,item,_=probe();resolve(m,ctx,item,treatment=.5);row=m.evidence[-1]
 assert row['status']=='negative_observed_pair' and row['states']['treatment']['individual_id'] not in m.chains[0]
 assert 'not universal' in row['hypotheses']['INTERVENTION_UNIT']

@pytest.mark.parametrize('score,validity',[(None,1),(.9,.99)])
def test_missing_or_partial_measurement_is_inconclusive_not_zero(score,validity):
 m,ctx,seed,item,_=probe();resolve(m,ctx,item,treatment=score,validity=validity);row=m.evidence[-1]
 assert row['status']=='inconclusive' and row['delta'] is None and row['states']['treatment']['score'] is None

def test_duplicate_callback_and_unplanned_source_never_fabricate_effect():
 m,ctx,seed,item,_=probe();p=m.probes[item.batch_id];ind,measurement=measured(ctx,item,p['expected_files']['control']);run(m.on_measured(ctx,ind,measurement));run(m.on_measured(ctx,ind,measurement));assert p['resolved']==1
 ind,measurement=measured(ctx,item,{'solution.py':'unplanned'},score=99);run(m.on_measured(ctx,ind,measurement));assert m.evidence[-1]['status']=='inconclusive'

def test_generation_failure_closes_without_template_retry():
 m,ctx,seed,item,_=probe()
 for _ in range(2):run(m.on_failed(ctx,Failure(item_id=item.id,batch_id=item.batch_id,stage='generate',reason='source_stale')))
 assert not m.probes and m.evidence[-1]['status']=='inconclusive' and m.probe_calls==1

def test_changed_source_before_dispatch_produces_nothing(monkeypatch):
 m,ctx,seed,item,_=probe();v=m.default_variator(model='unused');seed.genome.files['solution.py']+='changed';assert run(v.create(ctx,item))==[]

def test_resume_partial_preserves_raw_sources_and_archives_unresolved():
 m,ctx,seed,item,_=probe();ind,measurement=measured(ctx,item,m.probes[item.batch_id]['expected_files']['control']);run(m.on_measured(ctx,ind,measurement));saved=json.loads(json.dumps(m.state_dict()));new,_,_=fixture();new.load_state_dict(saved);new.reconcile(ctx)
 assert new.evidence[-1]['status']=='interrupted_unresolved' and new.evidence[-1]['executable_states']['control']==seed.genome.files and new.slots.probe==2

def test_decline_and_malformed_patch_spend_question_but_no_candidate_slots():
 for reply,status in [(' {"experiment":null,"reason":"not useful"}','declined')]:
  m,ctx,seed=fixture();anchor,owner=m._opportunity(ctx);backend(m,reply);assert not run(m._question(ctx,anchor,owner));assert m.scout_audit[-1]['status']==status and m.scout_questions==1 and m.slots.probe==0
 m,ctx,seed=fixture();anchor,owner=m._opportunity(ctx);backend(m,answer(seed,{'path':'solution.py','before':'missing','after':'x'}));assert not run(m._question(ctx,anchor,owner));assert m.scout_audit[-1]['status']=='invalid_source_edit' and m.slots.probe==0

def test_native_planner_pass_through_unchanged(monkeypatch):
 m,ctx,seed=fixture();seen=[]
 async def native(self,prompt):seen.append(prompt);return 'native'
 monkeypatch.setattr(Molt,'_llm',native);assert run(m._llm('unchanged native prompt'))=='native' and seen==['unchanged native prompt']

def test_ancestor_target_rejected_before_question_call():
 m,ctx,seed=fixture();direction_child(m,ctx,seed);_,owner=m._opportunity(ctx);calls=backend(m,answer(seed));assert not run(m._question(ctx,seed,owner));assert not calls and m.scout_questions==0

def test_actual_core_driver_remeasures_raw_control_despite_genome_dedup():
 from pantheon.evolution.core.loop import _run_create
 m,ctx,seed,item,_=probe();v=m.default_variator(model='unused');observed=[];prior=len(seed.measurements)
 class Evaluator:
  async def measure(self,context,ind,fidelity):
   observed.append(copy.deepcopy(ind.genome.files));value=.6 if ind.genome.files==seed.genome.files else .7
   return Measurement(individual_id=ind.id,fidelity='full',metrics={'combined_score':value,'validity':1})
 results=run(_run_create(item,ctx,v,{'code':Evaluator()}));assert len(observed)==2 and observed[0]==seed.genome.files
 assert results[0][0].id==seed.id and len(seed.measurements)==prior+1
 for ind,measurement in results:run(m.on_measured(ctx,ind,measurement))
 assert m.evidence[-1]['raw_anchor_control_exact'] and m.evidence[-1]['status']=='positive_observed_pair'
 assert m.slots.probe==2

def test_raw_cpp_control_and_edit_compile_to_the_expected_distinct_outputs(tmp_path):
 import subprocess
 files={'solution.cpp':'#include <iostream>\nint main(){std::cout << 1;}\n'}
 control,treatment=exact_edit(files,{'path':'solution.cpp','before':'std::cout << 1','after':'std::cout << 2'})
 assert control==files
 for name,source,wanted in [('control',control,'1'),('treatment',treatment,'2')]:
  cpp=tmp_path/(name+'.cpp');binary=tmp_path/name;cpp.write_text(source['solution.cpp'])
  subprocess.run(['g++','-std=c++20',str(cpp),'-o',str(binary)],check=True,capture_output=True,timeout=20)
  assert subprocess.run([str(binary)],check=True,capture_output=True,text=True,timeout=2).stdout==wanted

def test_source_context_changing_during_question_never_queues_a_patch():
 m,ctx,seed=fixture();anchor,owner=m._opportunity(ctx);reply=answer(seed)
 async def backend_change(prompt):
  seed.genome.files['solution.py']+='\n# changed source\n';return reply
 m._experiment_llm=backend_change;assert not run(m._question(ctx,anchor,owner));assert m.scout_audit[-1]['status']=='stale_context' and not m.requests

def test_low_fidelity_only_anchor_is_not_a_diagnostic_target():
 m,ctx,seed=fixture();seed.measurements[0].fidelity='low';assert m._full_score(seed) is None
 _,owner=(seed,{'intervention':0,'native_direction_id':'i0/d0','native_direction_type':'integration'});calls=backend(m,answer(seed));assert not run(m._question(ctx,seed,owner));assert not calls

def test_old_template_experiment_checkpoint_cannot_be_relabeled_as_raw_control():
 m,ctx,seed=fixture();state=m.state_dict();state.pop('patch_method');state['scout']['questions']=1
 with pytest.raises(ValueError):m.load_state_dict(state)

def test_cli_constructs_a_separate_patch_method_with_native_variator_configuration():
 from examples.evolution_bench.run_bench import build_method
 method=build_method('molt2_patch',0);assert isinstance(method,Molt2Patch) and method.name=='molt2_patch'
 variator=method.default_variator(model='unused',target_file='solution.cpp');assert isinstance(variator,MoltPatchVariator) and variator.discovery_owner is method

def test_whole_file_and_large_relative_rewrites_rejected_even_under_absolute_ceiling():
 with pytest.raises(ValueError):exact_edit({'solution.py':'def f(): return 1\n'},{'path':'solution.py','before':'def f(): return 1','after':'def f(): return 2'})
 source='a'*70+'b'*30
 with pytest.raises(ValueError):exact_edit({'solution.cpp':source},{'path':'solution.cpp','before':'a'*70,'after':'replacement'})

def test_retired_target_cannot_reset_native_stall_or_receive_patch_winner():
 from pantheon.evolution.methods.molt import RETIRED
 m,ctx,seed,item,_=probe();anchor_before=m.anchor;since_before=m.since
 for chain in m.probes[item.batch_id]['context']['chains']:m.role[chain]=RETIRED
 resolve(m,ctx,item);row=m.evidence[-1]
 assert row['status']=='positive_observed_pair' and m.anchor==anchor_before and m.since==since_before
 assert all(row['states']['treatment']['individual_id'] not in chain for chain in m.chains)

def test_incomplete_evaluation_retains_explicit_failure_reason():
 m,ctx,seed,item,_=probe();resolve(m,ctx,item,treatment=.9,validity=.99);row=m.evidence[-1]
 assert row['status']=='inconclusive' and row['errors'][0]['validity']==.99
 assert 'Incomplete' in row['errors'][0]['reason']

@pytest.mark.parametrize('stall,expect_probe',[(2,False),(12,True)])
def test_actual_evolve_driver_keeps_native_redesigns_and_remeasured_patch_pair_within80(monkeypatch,stall,expect_probe):
 import re
 from pantheon.evolution.core.loop import evolve
 from pantheon.evolution.core.method import Budget
 from pantheon.evolution.core.genome import CodeGenome
 from pantheon.evolution.core.work import Produced
 m=Molt2Patch(num_chains=2,k_candidates=2,num_inspirations=2,selector='rpucg',seed=0,stall_evals=stall)
 m._usage_cost=lambda:0.;questions=[];seen=[];counter=0;observed_context=[]
 async def planner(self,prompt):
  if prompt.startswith('You are removing'):
   pool=json.loads(prompt.split('Candidate directions:\n\n',1)[1]);return json.dumps({'kept_direction_ids':[next(iter(pool))]})
  if self.interventions:return json.dumps({'directions':[]})
  anchor=next(iter(observed_context[0].store)).id
  return json.dumps({'directions':[{'type':'redesign','target_branch_id':anchor,'task':'New independently refined implementation','mechanism_hypothesis':'Alternative representation','donor_branch_ids':[]}]})
 async def question_backend(self,prompt):
  questions.append(prompt);ident=re.search(r'\nAnchor ID: ([^\n]+)',prompt).group(1)
  return json.dumps({'experiment':{'anchor_id':ident,'mechanism':'exact return-expression edit','decision_to_inform':'keep this concrete alternative','predicted_contrast':'increase score','falsifier':'score does not increase','expected_cost':{'candidate_evaluations':2,'template_model_calls':0},'source_edit':{'path':'solution.py','before':'return x','after':'return x + 1'}}})
 async def native_create(self,context,item):
  nonlocal counter
  assert not item.meta.get('molt_patch_pair'),'Patch generation must bypass native/model generator'
  out=[]
  for _ in range(item.k):
   counter+=1;out.append(Produced(item_id=item.id,genome=CodeGenome(files={'solution.py':f'# native{counter}\ndef solve(x): return x\n'}),parent_ids=list(item.parent_ids),meta=dict(item.meta)))
  return out
 monkeypatch.setattr(Molt,'_llm',planner);monkeypatch.setattr(Molt2Patch,'_experiment_llm',question_backend);monkeypatch.setattr(MoltDiscoveryVariator,'create',native_create)
 class Evaluator:
  async def measure(self,context,ind,fidelity):
   code=ind.genome.files['solution.py'];seen.append(code);observed_context.append(context);value=.7 if 'return x + 1' in code else .61 if ind.parent_ids else .6
   return Measurement(individual_id=ind.id,fidelity='full',metrics={'combined_score':value,'validity':1})
 variator=m.default_variator(model='unused',target_file='solution.py')
 result=run(evolve(method=m,variator=variator,evaluators={'code':Evaluator()},seeds=[CodeGenome(files={'solution.py':'def solve(x): return x\n'})],objective='Public toy objective only',budget=Budget(max_items=100),concurrency=2))
 assert m.interventions and any(i['children'] for i in m.interventions) and m.sprouts
 assert len(seen)==80 and m.slots.native+m.slots.probe==80
 if not expect_probe:
  assert not questions and not m.evidence and m.slots.probe==0
  return
 assert questions and m.evidence
 assert m.probe_calls==len(questions)<=2 and m.slots.probe<=4 and not m.probes
 assert all(e['raw_anchor_control_exact'] and e['exact_source_edit'] for e in m.evidence)
 assert any(e['status']=='positive_observed_pair' for e in m.evidence)

def test_overlapping_matches_are_ambiguous_and_never_silently_choose_the_first():
 with pytest.raises(ValueError):exact_edit({'solution.cpp':'aaaa'},{'path':'solution.cpp','before':'aaa','after':'b'})
