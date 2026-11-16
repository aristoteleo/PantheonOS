"""Real source-parent topology regressions; no model or task calls."""
from test_molt2_scout import fixture,backend,answer
from test_molt2 import run
from pantheon.evolution.methods.molt import ESTABLISHED
from pantheon.evolution.core.genome import CodeGenome
from pantheon.evolution.core.individual import Individual
from pantheon.evolution.core.work import Measurement

def measured_child(ctx,parent,score,code):
 child=ctx.store.add(Individual(kind='code',genome=CodeGenome(files={'solution.py':code}),parent_ids=[parent.id]))
 child.measurements.append(Measurement(individual_id=child.id,metrics={'combined_score':score,'validity':1},fidelity='full'))
 return child

def direction_child(m,ctx,parent,score=.6,established=True):
 child=measured_child(ctx,parent,score,'# new native implementation\n')
 c=m._new_chain();m.chains[c].append(child.id)
 if established:m.role[c]=ESTABLISHED
 else:m.sprouts[c]={'fate':'probation'}
 m.interventions[0]['children'][0]['child']=child.id
 return child,c

def test_real_child_selected_instead_of_its_equally_good_ancestor():
 m,ctx,seed=fixture();child,_=direction_child(m,ctx,seed)
 anchor,owner=m._opportunity(ctx)
 assert anchor.id==child.id and owner['native_child_id']==child.id
 backend(m,answer(anchor));assert run(m._question(ctx,anchor,owner))
 assert m.requests[0]['context']['anchor_id']==child.id

def test_protected_child_does_not_cause_question_on_its_ancestor():
 m,ctx,seed=fixture();direction_child(m,ctx,seed,established=False)
 assert m._opportunity(ctx) is None and m.scout_questions==0 and m.probe_calls==0

def test_uncompetitive_child_does_not_redirect_probe_to_ancestor():
 m,ctx,seed=fixture();direction_child(m,ctx,seed,score=.4)
 assert m._opportunity(ctx) is None and m.probe_calls==0

def test_competitive_descendant_of_actual_direction_child_is_eligible():
 m,ctx,seed=fixture();child,c=direction_child(m,ctx,seed)
 descendant=measured_child(ctx,child,.61,'# repaired child\n');m.chains[c].append(descendant.id)
 anchor,owner=m._opportunity(ctx)
 assert anchor.id==descendant.id and owner['native_child_id']==child.id

def test_direct_ancestor_question_rejected_before_paid_call():
 m,ctx,seed=fixture();direction_child(m,ctx,seed);_,owner=m._opportunity(ctx);calls=backend(m,answer(seed))
 assert not run(m._question(ctx,seed,owner))
 assert not calls and m.scout_questions==0 and m.probe_calls==0

def test_queued_question_losing_child_lineage_spends_no_probe_slots():
 m,ctx,seed=fixture();child,c=direction_child(m,ctx,seed)
 descendant=measured_child(ctx,child,.61,'# descendant\n');m.chains[c].append(descendant.id)
 anchor,owner=m._opportunity(ctx);backend(m,answer(anchor));assert run(m._question(ctx,anchor,owner))
 descendant.parent_ids=[seed.id]
 assert m._probe_item(ctx) is None and m.slots.probe==0 and not m.requests
 assert m.rejected_requests[-1]['reason']=='anchor_no_longer_in_owner_branch'
