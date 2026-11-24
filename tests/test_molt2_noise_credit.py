import copy
import json
import pytest
from test_molt2_adaptive_branches import setup, resolve, run
from pantheon.evolution.methods.molt2_noise_credit import Molt2NoiseCredit
from pantheon.evolution.methods.molt2_adaptive_branches import Molt2AdaptiveBranches


def test_noise_gate_counts_all_evaluations_and_deduplicates_redelivery():
    m, ctx, _ = setup(Molt2NoiseCredit, noise_tolerance=.02)
    item = run(m.ask(ctx, 1))[0]
    resolve(m, ctx, item, .61, 'a')
    ind, measurement = resolve(m, ctx, item, .615, 'b')
    assert m.allocation_history[-1]['evaluations'] == 2
    assert m.allocation_history[-1]['success'] == 0
    before = copy.deepcopy(m.allocation_history)
    run(m.on_measured(ctx, ind, measurement))
    assert m.allocation_history == before
    assert m._posterior('chain:0') == (1, 3)


def test_fractional_credit_uses_prior_gains_not_current_gain():
    m, ctx, _ = setup(Molt2NoiseCredit, noise_tolerance=.01)
    m.allocation_history = [dict(arm='chain:0', evaluations=2, success=1, frontier_gain=.2)]
    item = run(m.ask(ctx, 1))[0]
    resolve(m, ctx, item, .65, 'a')
    resolve(m, ctx, item, .7, 'b')
    row = m.allocation_history[-1]
    assert row['frontier_gain'] == pytest.approx(.1)
    assert row['success'] == pytest.approx(.45)
    assert m._posterior('chain:0') == pytest.approx((2.45, 3.55))


def test_checkpoint_retains_credit_history_and_tolerance():
    m, ctx, _ = setup(Molt2NoiseCredit, noise_tolerance=.01)
    item = run(m.ask(ctx, 1))[0]
    resolve(m, ctx, item, .8, 'a')
    resolve(m, ctx, item, .7, 'b')
    state = json.loads(json.dumps(m.state_dict()))
    clone = Molt2NoiseCredit(noise_tolerance=99)
    clone.load_state_dict(state)
    assert clone.noise_tolerance == .01
    assert clone.allocation_history == m.allocation_history
    assert clone._posterior('chain:0') == m._posterior('chain:0')


@pytest.mark.parametrize('noise', [-1, float('nan'), float('inf')])
def test_invalid_noise_rejected(noise):
    with pytest.raises(ValueError):
        Molt2NoiseCredit(noise_tolerance=noise)


def test_native_prefix_parent_choices_and_rng_are_unchanged():
    a, ca, _ = setup(Molt2AdaptiveBranches)
    b, cb, _ = setup(Molt2NoiseCredit, noise_tolerance=.01)
    for step in range(3):
        ia, ib = run(a.ask(ca, 1))[0], run(b.ask(cb, 1))[0]
        assert ia.parent_ids == ib.parent_ids
        assert ia.meta['chain'] == ib.meta['chain']
        assert a.rng.getstate() == b.rng.getstate()
        for k in range(2):
            for method, ctx, item in [(a, ca, ia), (b, cb, ib)]:
                from pantheon.evolution.core import Individual, CodeGenome, Measurement
                child = ctx.store.add(Individual(id=f'prefix-{step}-{k}',
                    genome=CodeGenome(files={'solution.cpp': f'int main(){{return {step+k};}}'}),
                    kind='code', parent_ids=item.parent_ids))
                measurement = Measurement(individual_id=child.id, metrics={'combined_score': .6+step*.01, 'validity': 1},
                                          item_id=item.id, batch_id=item.batch_id)
                ctx.store.record(measurement)
                run(method.on_measured(ctx, child, measurement))
        assert a.chains == b.chains


def test_runner_factory_requires_explicit_noise_tolerance():
    from examples.evolution_bench.run_bench import build_method
    with pytest.raises(ValueError, match='explicitly frozen'):
        build_method('molt2_noise_credit', 42)
    method = build_method('molt2_noise_credit', 42, ln_opts={'noise_tolerance': .012})
    assert isinstance(method, Molt2NoiseCredit)
    assert method.noise_tolerance == .012
