from pantheon.evolution.methods.molt2_lns_vns2 import Molt2LNSVNS2


def test_failed_neighborhoods_escalate_before_planning_and_saturate():
    method = Molt2LNSVNS2()
    assert method.next_portfolio_scope() == 1
    for scope, expected in [(1,2),(2,3),(3,3)]:
        method.portfolios = [{'round':0,'scope':scope}]
        method.operators = {'ordinary':{'rewards':[1.]}, 'round0/family0':{'rewards':[0.,0.]}}
        assert method.next_portfolio_scope() == expected


def test_only_current_portfolio_frontier_contributions_reset_scope():
    method = Molt2LNSVNS2()
    method.portfolios = [{'round':1,'scope':2}]
    method.operators = {'ordinary':{'rewards':[1.]},'round0/family0':{'rewards':[1.]},'round1/family0':{'rewards':[0.]}}
    assert method.next_portfolio_scope() == 3
    method.operators['round1/family0']['rewards'] = [method.progress_rel * 2]
    assert method.next_portfolio_scope() == 1


def test_checkpoint_preserves_evidence_for_scope_decision():
    method = Molt2LNSVNS2()
    method.portfolios = [{'round':0,'scope':2}]
    method.operators['round0/family0'] = {'rewards':[0.], 'active':True}
    clone = Molt2LNSVNS2()
    clone.load_state_dict(method.state_dict())
    assert clone.next_portfolio_scope() == 3


def test_planner_and_validator_receive_escalated_scope(monkeypatch):
    import asyncio
    from pantheon.evolution.methods.molt2_lns import Molt2LNS
    seen = []
    async def planner(self, ctx):
        seen.append(self.scope)
    monkeypatch.setattr(Molt2LNS, '_make_portfolio', planner)
    method = Molt2LNSVNS2()
    method.portfolios = [{'round':0,'scope':1}]
    method.scope = 1
    asyncio.run(method._make_portfolio(None))
    assert seen == [2]


def test_ordinary_gain_does_not_shrink_neighborhood(monkeypatch):
    import asyncio
    from pantheon.evolution.methods.molt2_lns import Molt2LNS
    async def measured(self, ctx, individual, measurement):
        self.scope = 1
        self.stall_count = 0
    monkeypatch.setattr(Molt2LNS, 'on_measured', measured)
    method = Molt2LNSVNS2()
    method.scope = 3
    asyncio.run(method.on_measured(None, None, None))
    assert method.scope == 3 and method.stall_count == 0
