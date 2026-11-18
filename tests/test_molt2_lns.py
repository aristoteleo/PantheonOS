"""Real source preservation, callbacks, portfolio boundaries and scheduler behavior."""
import asyncio,copy,json
import pytest
from pantheon.evolution.core import Budget,EvolveContext,Individual,Measurement,Store
from pantheon.evolution.core.genome import CodeGenome
from pantheon.evolution.core.work import Failure
from pantheon.evolution.methods.molt2_lns import Molt2LNS,apply_replacements,validate_regions,source_hash

CODE='# EVOLVE-BLOCK-START\nint prepare(){return 1;}\nint solve(){return prepare();}\nint main(){return solve();}\n// EVOLVE-BLOCK-END\n'
def run(x):return asyncio.run(x)
def make(**kw):
 store=Store();seed=store.add(Individual(genome=CodeGenome(files={'solution.cpp':CODE,'keep.txt':'unchanged\n'}),kind='code'))
 seed.measurements.append(Measurement(individual_id=seed.id,metrics={'combined_score':.6,'validity':1.},fidelity='full'))
 m=Molt2LNS(num_chains=2,k_candidates=2,num_inspirations=2,selector='rpucg',seed=42,**kw)
 ctx=EvolveContext(store=store,budget=Budget(max_items=100),objective='Generic objective',concurrency=2);run(m.start(ctx,[seed]));return m,ctx,seed
def family(name='solver',line=3):return {'name':name,'decision':'algorithmic decision '+name,'hypothesis':'new mechanism improves score','replacement':'replace current search','preserve':'all other code','regions':[{'path':'solution.cpp','start_line':line,'end_line':line}]}
def plan(m,ctx,families=None):
 async def llm(prompt):return json.dumps({'families':families or [family()]})
 m._llm=llm;run(m._make_portfolio(ctx));return next(k for k in m.operators if k!='ordinary')
def measured(m,ctx,item,score,meta=None,files=None):
 if meta is not None and 'lns_edit' in item.meta:meta={**meta,'lns_arm':item.meta['lns_edit']['arm']}
 i=ctx.store.add(Individual(genome=CodeGenome(files=files or {'solution.cpp':CODE.replace('return prepare();',f'return {score};'),'keep.txt':'unchanged\n'}),kind='code',parent_ids=item.parent_ids,meta=meta or {}))
 z=Measurement(individual_id=i.id,metrics={'combined_score':score,'validity':1.},batch_id=item.batch_id,item_id=item.id,fidelity='full');i.measurements.append(z);run(m.on_measured(ctx,i,z));return i,z

def test_source_outside_region_and_other_files_are_identical():
 files={'solution.cpp':CODE,'keep.txt':'unchanged\n'};before=copy.deepcopy(files)
 new,masks=apply_replacements(files,[{'path':'solution.cpp','start_line':3,'end_line':3}],[{'region':0,'after':'int solve(){\n return 2;\n}'}],1)
 assert files==before and new['keep.txt']==files['keep.txt']
 assert new['solution.cpp'].startswith('# EVOLVE-BLOCK-START\nint prepare(){return 1;}\n')
 assert new['solution.cpp'].endswith('int main(){return solve();}\n// EVOLVE-BLOCK-END\n')
 assert masks==[{'path':'solution.cpp','start_line':3,'end_line':5}]
 newer,mask=apply_replacements(new,masks,[{'region':0,'after':'int solve(){return 3;}'}],1)
 assert mask==[{'path':'solution.cpp','start_line':3,'end_line':3}]
 assert 'int prepare(){return 1;}' in newer['solution.cpp']

@pytest.mark.parametrize('regions', [[],[{'path':'new','start_line':1,'end_line':1}],[{'path':'solution.cpp','start_line':0,'end_line':2}],[{'path':'solution.cpp','start_line':1,'end_line':3}],[{'path':'solution.cpp','start_line':3,'end_line':3},{'path':'solution.cpp','start_line':3,'end_line':4}]])
def test_invalid_regions_rejected(regions):
 with pytest.raises(ValueError):validate_regions({'solution.cpp':CODE},regions,2)

def test_scope_boundaries_and_coupled_regions():
 r=[{'path':'solution.cpp','start_line':2,'end_line':2},{'path':'solution.cpp','start_line':4,'end_line':4}]
 with pytest.raises(ValueError):validate_regions({'solution.cpp':CODE},r,1)
 assert len(validate_regions({'solution.cpp':CODE},r,2))==2

@pytest.mark.parametrize('replacements',[[],[{'region':0,'after':''}],[{'region':0,'after':'// EVOLVE-BLOCK-END'}],[{'region':1,'after':'x'}],[{'region':0,'after':'int solve(){return prepare();}'}]])
def test_invalid_or_noop_replacement_does_not_generate_a_candidate(replacements):
 with pytest.raises(ValueError):apply_replacements({'solution.cpp':CODE},family()['regions'],replacements,1)

def test_portfolio_is_generic_and_full_anchor_is_supplied():
 m,ctx,seed=make();prompts=[]
 async def llm(prompt):prompts.append(prompt);return json.dumps({'families':[family(),family('initialization',2)]})
 m._llm=llm;run(m._make_portfolio(ctx));assert len(m.portfolios[0]['families'])==2
 assert 'int prepare(){return 1;}' in prompts[0] and 'Generic objective' in prompts[0]
 assert not any(t in prompts[0] for t in ['Spateo','ahc024','ahc058'])
 assert m.portfolios[0]['anchor_source_hash']==source_hash(seed.genome.files)


def test_duplicate_region_proposals_are_not_counted_as_diversity():
 m,ctx,_=make();plan(m,ctx,[family(),family('same region')]);assert len(m.portfolios[0]['families'])==1


def test_ordinary_reservation_and_ucb_over_operators():
 m,ctx,_=make();arm=plan(m,ctx);assert m._choose_operator(['ordinary',arm])=='ordinary'
 m.completed_batches=1;m.operator_history=[('ordinary',.1)];assert m._choose_operator(['ordinary',arm])==arm
 m.operator_history=[('ordinary',.01),(arm,.3)];assert m._choose_operator(['ordinary',arm])==arm
 # Forget stale reward and counts in the global window, not one window per operator.
 m.operator_window=2;m.operator_history=[(arm,1),('ordinary',.1),('ordinary',.1)];assert m._choose_operator(['ordinary',arm])==arm


def test_initial_regression_refined_without_losing_global_champion():
 m,ctx,seed=make();arm=plan(m,ctx);m.completed_batches=1;m.operator_history=[('ordinary',0.)]
 item=run(m.ask(ctx,1))[0];assert item.meta['lns_edit']['anchor']==seed.id
 masks=family()['regions'];first,_=measured(m,ctx,item,.4,{'lns_regions':masks});second,_=measured(m,ctx,item,.5,{'lns_regions':masks})
 c=m.operators[arm]['chain'];assert m.chains[c]==[second.id] and seed.id in m.chains[0]
 assert m._global_best(ctx)==.6 and m.operators[arm]['rewards']==[0.]
 m.completed_batches=1;m.operator_history=[('ordinary',0.)]
 nxt=run(m.ask(ctx,1))[0];assert nxt.meta['lns_edit']['anchor']==second.id


def test_reward_uses_dispatch_frontier_not_easy_improvement_from_a_bad_parent():
 m,ctx,_=make();arm=plan(m,ctx);m.completed_batches=1;m.operator_history=[('ordinary',0.)];item=run(m.ask(ctx,1))[0]
 measured(m,ctx,item,.4,{'lns_regions':family()['regions']});measured(m,ctx,item,.5,{'lns_regions':family()['regions']})
 assert m.operator_history[-1]==(arm,0.)


def test_duplicate_measurement_not_double_counted():
 m,ctx,_=make();item=run(m.ask(ctx,1))[0];ind,z=measured(m,ctx,item,.7);run(m.on_measured(ctx,ind,z))
 assert len(m.seen_measurements)==1 and m.edit_batches[item.batch_id]['resolved']==1


def test_generation_failure_closes_and_does_not_fake_an_evaluation():
 m,ctx,_=make();item=run(m.ask(ctx,1))[0];f=Failure(item_id=item.id,batch_id=item.batch_id,reason='bad JSON')
 run(m.on_failed(ctx,f));run(m.on_failed(ctx,f));assert m.edit_batches[item.batch_id]['resolved']==1
 run(m.on_failed(ctx,Failure(item_id=item.id,batch_id=item.batch_id,reason='another missing child')))
 assert not m.open and not m.edit_batches and not m.seen_measurements and m.completed_batches==1


def test_plan_at_stall_even_with_other_work_in_flight():
 m,ctx,_=make(stall_evals=2);first=run(m.ask(ctx,1))[0];measured(m,ctx,first,.5);measured(m,ctx,first,.5)
 # Fill another original chain, then planning must still be able to happen.
 run(m.ask(ctx,1));assert m.open
 async def llm(prompt):return json.dumps({'families':[family()]})
 m._llm=llm;run(m.ask(ctx,1));assert len(m.portfolios)==1


def test_source_materialization_and_updated_masks_from_real_variator():
 m,ctx,seed=make();arm=plan(m,ctx);m.completed_batches=1;m.operator_history=[('ordinary',0.)];item=run(m.ask(ctx,1))[0]
 async def llm(prompt):
  assert 'int prepare(){return 1;}' in prompt
  return json.dumps({'replacements':[{'region':0,'after':'int solve(){return 9;}'}]})
 m._llm=llm;v=m.default_variator(model='unused',target_file='solution.cpp');produced=run(v.create(ctx,item))
 assert len(produced)==2 and all(p.meta['lns_arm']==arm for p in produced)
 assert all(p.genome.files['keep.txt']=='unchanged\n' for p in produced)
 assert seed.genome.files['solution.cpp']==CODE


def test_stale_source_before_dispatch_is_rejected():
 m,ctx,seed=make();plan(m,ctx);m.completed_batches=1;m.operator_history=[('ordinary',0.)];item=run(m.ask(ctx,1))[0];seed.genome.files['solution.cpp']+='changed'
 v=m.default_variator(model='unused');assert run(v.create(ctx,item))==[]


def test_checkpoint_preserves_selection_state_and_marks_unresolved():
 m,ctx,_=make();plan(m,ctx);item=run(m.ask(ctx,1))[0];saved=json.loads(json.dumps(m.state_dict()));new,_,_=make();new.load_state_dict(saved)
 assert new.operators==m.operators and new.operator_history==m.operator_history
 assert new.generation_audit[-1]['status']=='interrupted_unresolved_no_reward_assigned'
 assert not new.edit_batches


def test_portfolio_not_replaced_before_all_active_families_have_refinement_feedback():
 m,ctx,_=make(stall_evals=2);plan(m,ctx,[family(),family('other',2)])
 m.stall_count=100;m.last_plan_at=0;m.seen_measurements={(str(i),'x','y',i) for i in range(100)}
 async def forbidden(prompt):raise AssertionError('Do not discard untested families')
 m._llm=forbidden
 run(m.ask(ctx,1));assert len(m.portfolios)==1
