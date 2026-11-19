import math
from pantheon.evolution.methods.molt2_lns_adaptive import Molt2LNSAdaptive
from pantheon.evolution.methods.molt2_lns_global import Molt2LNSGlobal
from pantheon.evolution.methods.molt2_lns_feedback import LNSFeedbackVariator


def prepared(cls=Molt2LNSAdaptive):
    m=cls();m.completed_batches=1
    m.operators.update(good={'rewards':[]},bad={'rewards':[]})
    m.operator_history=[('good',.001)]*3+[('bad',0.)]
    return m


def test_reward_scale_invariance_across_twelve_orders_of_magnitude():
    for factor in [1e-6,1.,1e6]:
        m=prepared();m.operator_history=[(arm,r*factor) for arm,r in m.operator_history]
        assert m._choose_operator(['good','bad'])=='good'


def test_positive_evidence_can_compete_with_exploration_at_observed_reward_scale():
    # Same historical outcomes, same exploration constant, different reward units.
    assert prepared(Molt2LNSGlobal)._choose_operator(['good','bad'])=='bad'
    assert prepared()._choose_operator(['good','bad'])=='good'


def test_backbone_reservation_and_unseen_operator_exploration_remain():
    m=prepared();m.completed_batches=2
    assert m._choose_operator(['ordinary','good'])=='ordinary'
    m.completed_batches=1
    assert m._choose_operator(['good','unseen'])=='unseen'
    assert m._choose_operator([]) is None


def test_zero_and_nonfinite_rewards_do_not_break_the_selector():
    m=prepared();m.operator_history=[('good',0.),('bad',0.),('good',float('nan'))]
    assert m._choose_operator(['good','bad']) in ['good','bad']


def test_combined_candidate_uses_feedback_writer_and_checkpoint_state():
    m=prepared();assert isinstance(m.default_variator(model='unused'),LNSFeedbackVariator)
    clone=Molt2LNSAdaptive();clone.load_state_dict(m.state_dict())
    assert clone._choose_operator(['good','bad'])=='good'


def test_distinct_mechanisms_can_reopen_the_same_function():
    from test_molt2_lns import CODE,family
    from pantheon.evolution.methods.molt2_lns import Molt2LNS
    proposals=[family('anneal'),family('beam')]
    proposals[0]['replacement']='Use simulated annealing'
    proposals[1]['replacement']='Use beam search'
    assert len(Molt2LNS()._accept_families({'solution.cpp':CODE},proposals))==1
    assert len(Molt2LNSAdaptive()._accept_families({'solution.cpp':CODE},proposals))==2


def test_exact_proposal_duplicates_and_invalid_markers_still_rejected():
    from test_molt2_lns import CODE,family
    a=family('first');b=dict(a,name='second');bad=family('bad',line=1)
    method=Molt2LNSAdaptive()
    assert len(method._accept_families({'solution.cpp':CODE},[a,b,bad]))==1


def test_full_program_option_keeps_concrete_planner_idea():
    from test_molt2_lns import family
    method=Molt2LNSAdaptive();original=family('anneal');original['replacement']='Use simulated annealing'
    masks=[{'path':'solution.cpp','start_line':2,'end_line':5}]
    expanded=method._global_family(original,masks)
    assert expanded['name']=='anneal' and expanded['replacement'].startswith('Use simulated annealing')
    assert expanded['regions']==masks and expanded['whole_program_expansion']
    assert original['regions']!=masks


def test_real_planner_can_keep_same_region_strategies_then_expand_one():
    import json
    from test_molt2_lns import make,family,run
    method,ctx,seed=make();method.__class__=Molt2LNSAdaptive
    first=family('anneal');first['replacement']='Use simulated annealing'
    second=family('beam');second['replacement']='Use beam search'
    async def llm(prompt):return json.dumps({'families':[first,second]})
    method._llm=llm;run(method._make_portfolio(ctx))
    record=method.portfolios[-1]
    assert len(record['families'])==2
    assert record['families'][1]['name']=='beam'
    arm=record['explicit_global_arm']
    assert method.operators[arm]['strategy']['replacement'].startswith('Use beam search')
