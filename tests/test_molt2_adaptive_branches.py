import asyncio
import copy
import json
from pantheon.evolution.core import Budget, CodeGenome, EvolveContext, Individual, Measurement, Store
from pantheon.evolution.core.work import Failure
from pantheon.evolution.methods.molt_modified import ModifiedMolt
from pantheon.evolution.methods.molt2_adaptive_branches import Molt2AdaptiveBranches
from pantheon.evolution.methods.molt import SPROUT, BASE, RETIRED
from pantheon.evolution.variators.completion import EvolveBlock

CODE = '// EVOLVE-BLOCK-START\nint main(){return 0;}\n// EVOLVE-BLOCK-END\n'
run = asyncio.run

def setup(cls=Molt2AdaptiveBranches, **kwargs):
    store = Store()
    seed = store.add(Individual(id='seed', genome=CodeGenome(files={'solution.cpp': CODE}), kind='code'))
    seed.measurements.append(Measurement(individual_id=seed.id, metrics={'combined_score': .6, 'validity': 1}))
    ctx = EvolveContext(store=store, budget=Budget(max_items=100), objective='Generic task', concurrency=2)
    m = cls(num_chains=2, k_candidates=2, num_inspirations=2, selector='rpucg', seed=42, stall_evals=1000, **kwargs)
    run(m.start(ctx, [seed]))
    return m, ctx, seed

def resolve(m, ctx, item, score, suffix='0'):
    ind = ctx.store.add(Individual(id=item.batch_id+suffix, genome=CodeGenome(files={'solution.cpp':CODE+'\n'+suffix}), kind='code', parent_ids=item.parent_ids))
    mm = Measurement(individual_id=ind.id, metrics={'combined_score':score,'validity':1}, item_id=item.id, batch_id=item.batch_id)
    ctx.store.record(mm)
    run(m.on_measured(ctx, ind, mm))
    return ind, mm

def intervention(m, seed):
    m.interventions.append({'n':0,'children':[]})
    return {'id':'d0','intervention':0,'type':'redesign','target_branch_id':seed.id,
            'task':'Change the mechanism','mechanism_hypothesis':'A different mechanism can improve the objective.'}

def test_pre_intervention_chain_order_rng_and_prompts_match_modified_molt():
    a, ca, sa = setup(ModifiedMolt)
    b, cb, sb = setup()
    va = a.default_variator(model='unused', target_file='solution.cpp')
    vb = b.default_variator(model='unused', target_file='solution.cpp')
    for step in range(4):
        ia, ib = run(a.ask(ca,1))[0], run(b.ask(cb,1))[0]
        assert ia.meta['chain']==ib.meta['chain'] and ia.parent_ids==ib.parent_ids
        assert a.rng.getstate()==b.rng.getstate()
        assert va.build_block_prompt(ca,ia,'solution.cpp',EvolveBlock(CODE)) == vb.build_block_prompt(cb,ib,'solution.cpp',EvolveBlock(CODE))
        # Same deterministic tape, with identical IDs in two independent stores.
        for k in range(2):
            for m,ctx,it in [(a,ca,ia),(b,cb,ib)]:
                ind=ctx.store.add(Individual(id=f'p{step}-{k}',genome=CodeGenome(files={'solution.cpp':CODE+f'\n//{step}-{k}'}),kind='code',parent_ids=it.parent_ids))
                mm=Measurement(individual_id=ind.id,metrics={'combined_score':.6+.01*step,'validity':1},batch_id=it.batch_id,item_id=it.id)
                ctx.store.record(mm);run(m.on_measured(ctx,ind,mm))
        assert a.chains==b.chains and a.selector.state_dict()==b.selector.state_dict()

def test_cpp_redesign_shows_complete_selected_target_and_keeps_lineage():
    m,ctx,seed=setup(ModifiedMolt);m.evolve_file='solution.cpp';d=intervention(m,seed)
    item=m._direction_item(ctx,d)
    assert item.context.parents==[seed] and item.parent_ids==[seed.id]
    v=m.default_variator(model='unused',target_file='solution.cpp')
    prompt=v.build_block_prompt(ctx,item,'solution.cpp',EvolveBlock(CODE))
    assert CODE in prompt and 'Start from the complete working TARGET program' in prompt
    clone=ModifiedMolt();clone.load_state_dict(json.loads(json.dumps(m.state_dict())))
    assert clone.target_context_audit==m.target_context_audit


def test_evolved_target_replaces_the_shorter_seed_reference():
    from pantheon.evolution.methods.molt import Molt
    m,ctx,seed=setup(ModifiedMolt);m.evolve_file='solution.cpp'
    code=CODE+'\nint evolved_helper(){return 42;}\n'
    target=ctx.store.add(Individual(id='strong_evolved',genome=CodeGenome(files={'solution.cpp':code}),kind='code',parent_ids=[seed.id]))
    ctx.store.record(Measurement(individual_id=target.id,metrics={'combined_score':.8,'validity':1}))
    m.chains[0].append(target.id)
    d=intervention(m,seed);d['target_branch_id']=target.id
    native=Molt._direction_item(m,ctx,dict(d))
    changed=m._direction_item(ctx,dict(d))
    assert native.context.parents[0].id==seed.id
    assert changed.context.parents[0].id==target.id and changed.parent_ids==[target.id]
    assert changed.context.parents[0].genome.files['solution.cpp']==code

def test_champion_refinement_can_precede_pending_redesign():
    m,ctx,seed=setup();m.pending=[intervention(m,seed)]
    item=run(m.ask(ctx,1))[0]
    assert item.meta['molt2_allocation']['reason']=='incumbent_floor'
    assert 'molt_prompt' not in item.meta and len(m.pending)==1

def test_ordinary_refinement_and_new_proposals_are_both_schedulable():
    m,ctx,seed=setup();m.pending=[intervention(m,seed)];m.allocation_dispatches=1
    m._choose=lambda *_:('proposal','test',{})
    item=run(m.ask(ctx,1))[0]
    assert item.meta['molt_type']=='redesign' and not m.pending
    resolve(m,ctx,item,.5)
    new_chain=max(m.sprouts);assert m._turns(new_chain)==1
    del m._choose
    arm,reason,_=m._choose(ctx,m._available(ctx),False)
    assert arm==f'chain:{new_chain}' and reason=='minimum_probe'

def test_reward_charges_all_children_and_credits_only_frontier_improvement():
    m,ctx,seed=setup();item=run(m.ask(ctx,1))[0]
    resolve(m,ctx,item,.55,'0');assert not m.allocation_history
    ind,mm=resolve(m,ctx,item,.7,'1')
    row=m.allocation_history[-1];assert row['evaluations']==2 and row['success']==1
    history=copy.deepcopy(m.allocation_history);attempts=m.attempts
    run(m.on_measured(ctx,ind,mm));assert m.allocation_history==history and m.attempts==attempts

def test_progress_from_a_weak_branch_does_not_receive_false_global_credit():
    m,ctx,seed=setup();item=run(m.ask(ctx,1))[0]
    resolve(m,ctx,item,.2,'0');resolve(m,ctx,item,.5,'1')
    assert m.allocation_history[-1]['success']==0

def test_generation_failure_does_not_consume_evaluation_observations():
    m,ctx,seed=setup();item=run(m.ask(ctx,1))[0]
    for _ in range(2):run(m.on_failed(ctx,Failure(item_id=item.id,batch_id=item.batch_id,stage='generate',reason='empty')))
    assert not m.allocation_history and not m._allocation_open and m.attempts==0

def test_evaluation_failure_is_charged_and_concurrent_open_chain_is_not_chosen():
    m,ctx,seed=setup();items=run(m.ask(ctx,2));assert len(items)==2
    assert m._available(ctx)==[]
    for _ in range(2):run(m.on_failed(ctx,Failure(item_id=items[0].id,batch_id=items[0].batch_id,stage='evaluate',reason='invalid')))
    assert m.allocation_history[-1]['evaluations']==2 and m.allocation_history[-1]['success']==0

def test_retired_chains_are_excluded_and_window_counts_evaluations():
    m,ctx,seed=setup(history_evals=3);m.role[1]=RETIRED
    assert m._available(ctx)==[0]
    for j in range(2):
        it=run(m.ask(ctx,1))[0];resolve(m,ctx,it,.7+j*.01,'0');resolve(m,ctx,it,.7+j*.01,'1')
    assert len(m.allocation_history)==1 and sum(x['evaluations'] for x in m.allocation_history)<=3

def test_checkpoint_roundtrip_preserves_posterior_and_next_random_choice():
    m,ctx,seed=setup();item=run(m.ask(ctx,1))[0];resolve(m,ctx,item,.7,'0');resolve(m,ctx,item,.7,'1')
    clone=Molt2AdaptiveBranches();clone.load_state_dict(json.loads(json.dumps(m.state_dict())));clone.rng.setstate(m.rng.getstate())
    m.allocation_dispatches=clone.allocation_dispatches=1
    assert m._choose(ctx,[0,1],True)==clone._choose(ctx,[0,1],True)
    assert clone.allocation_history==m.allocation_history and clone.incumbent_every==m.incumbent_every

def test_posterior_prefers_a_productive_branch_over_repeated_failures():
    m,ctx,seed=setup();m.allocation_dispatches=1
    m.allocation_history=([{'arm':'chain:0','evaluations':2,'success':0} for _ in range(10)]
                          + [{'arm':'chain:1','evaluations':2,'success':1} for _ in range(10)])
    choices=[m._choose(ctx,[0,1],False)[0] for _ in range(100)]
    assert choices.count('chain:1')>95


def test_gone_planner_targets_fall_back_to_available_ordinary_work():
    m,ctx,seed=setup();d=intervention(m,seed);d['target_branch_id']='gone';m.pending=[d]
    m.allocation_dispatches=1
    original=m._choose
    m._choose=lambda ctx,available,pending: ('proposal','test',{}) if pending else original(ctx,available,pending)
    item=run(m.ask(ctx,1))[0]
    assert item.meta['chain'] in [0,1] and not m.pending
    assert m.interventions[0]['children'][0]['skipped']=='target gone'
