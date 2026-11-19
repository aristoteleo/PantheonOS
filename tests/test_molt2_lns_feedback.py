import asyncio,json
from types import SimpleNamespace
from test_molt2_lns import make
from pantheon.evolution.methods.molt2_lns import LNSVariator
from pantheon.evolution.methods.molt2_lns_feedback import LNSFeedbackVariator


def test_anchor_metrics_reach_writer_without_mutating_shared_context(monkeypatch):
    method,ctx,seed=make();seen=[]
    async def capture(self,local,item):
        seen.append(local);return []
    monkeypatch.setattr(LNSVariator,'create',capture)
    item=SimpleNamespace(meta={'lns_edit':{'anchor':seed.id}},context=SimpleNamespace(parents=[seed],history='past scores'))
    original=ctx.objective
    asyncio.run(LNSFeedbackVariator.create(object.__new__(LNSFeedbackVariator),ctx,item))
    assert ctx.objective==original
    assert seen[0] is not ctx and seen[0].store is ctx.store
    payload=json.loads(seen[0].objective.split('MEASURED FEEDBACK (training evaluator only):\n')[1])
    assert payload['anchor']['metrics']['validity']==1
    assert payload['anchor']['metrics']['combined_score']==.6
    assert payload['chain_history']=='past scores'


def test_ordinary_search_receives_exact_unmodified_context(monkeypatch):
    method,ctx,seed=make();seen=[]
    async def capture(self,local,item):seen.append(local);return []
    monkeypatch.setattr(LNSVariator,'create',capture)
    asyncio.run(LNSFeedbackVariator.create(object.__new__(LNSFeedbackVariator),ctx,SimpleNamespace(meta={})))
    assert seen==[ctx]


def test_real_writer_prompt_contains_feedback_and_materializes_valid_edit():
    from test_molt2_lns import plan,run
    from pantheon.evolution.methods.molt2_lns_feedback import Molt2LNSFeedback
    method,ctx,seed=make();method.__class__=Molt2LNSFeedback
    plan(method,ctx);method.completed_batches=1;method.operator_history=[('ordinary',0.)]
    item=run(method.ask(ctx,1))[0];prompts=[]
    async def llm(prompt):
        prompts.append(prompt)
        return json.dumps({'replacements':[{'region':0,'after':'int solve(){return 2;}'}]})
    method._llm=llm
    variator=method.default_variator(model='unused',target_file='solution.cpp')
    results=run(variator.create(ctx,item))
    assert len(results)==2
    assert all('MEASURED FEEDBACK' in p and 'combined_score' in p and 'validity' in p for p in prompts)
    assert ctx.objective=='Generic objective'
