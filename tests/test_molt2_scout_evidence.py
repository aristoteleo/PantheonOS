"""Public evidence provenance and missing-observation semantics; no paid calls."""
from test_molt2_scout import fixture
from test_molt2_scout_lineage import direction_child,measured_child
from pantheon.evolution.core.work import Measurement

def test_exact_native_parent_child_and_anchor_observations_without_causal_claim():
 m,ctx,seed=fixture();child,c=direction_child(m,ctx,seed,score=.6);anchor=measured_child(ctx,child,.61,'# improved child\n');m.chains[c].append(anchor.id);_,owner=m._opportunity(ctx)
 data=m._public_observations(ctx,anchor,owner);rows={r['individual_id']:r for r in data['programs']}
 assert set(rows)=={seed.id,child.id,anchor.id}
 assert rows[anchor.id]['public_score']==.61 and rows[child.id]['public_score']==.6
 assert 'child_parent' in rows[seed.id]['roles'] and 'anchor_parent' in rows[child.id]['roles']
 assert 'cannot establish component causality' in data['interpretation']
 prompt=m._question_prompt(ctx,anchor,owner)
 assert 'Observed public program context' in prompt and 'You may decline' in prompt
 assert 'already present but never isolated' in prompt

def test_missing_invalid_and_partial_fidelity_observations_are_not_imputed():
 m,ctx,seed=fixture();child,c=direction_child(m,ctx,seed);_,owner=m._opportunity(ctx)
 seed.measurements=[Measurement(individual_id=seed.id,metrics={'combined_score':99.,'validity':.99},fidelity='full'),Measurement(individual_id=seed.id,metrics={'combined_score':100.,'validity':1},fidelity='low')]
 row=next(r for r in m._public_observations(ctx,child,owner)['programs'] if r['individual_id']==seed.id)
 assert row['public_score'] is None and row['measurement_status']=='no_fully_valid_full_observation'

def test_first_valid_observation_not_max_or_later_lucky_reread():
 m,ctx,seed=fixture();child,c=direction_child(m,ctx,seed,score=.6);_,owner=m._opportunity(ctx)
 child.measurements.append(Measurement(individual_id=child.id,metrics={'combined_score':100.,'validity':1},fidelity='full'))
 row=next(r for r in m._public_observations(ctx,child,owner)['programs'] if r['individual_id']==child.id)
 assert row['public_score']==.6
