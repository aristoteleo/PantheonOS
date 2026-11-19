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
