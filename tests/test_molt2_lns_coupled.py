import json
from test_molt2_lns import make, family, run, CODE
from pantheon.evolution.methods.molt2_lns_coupled import Molt2LNSCoupled
from pantheon.evolution.methods.molt2_lns import apply_replacements
from pantheon.evolution.methods.molt2_lns_global import whole_program_regions


def prepared():
    method, ctx, seed = make()
    method.__class__ = Molt2LNSCoupled
    proposals = [family('change_helper', 2), family('change_solver', 3)]
    proposals[0]['replacement'] = 'Replace helper with a different mechanism'
    proposals[1]['replacement'] = 'Replace solve with another mechanism'
    async def llm(prompt):
        return json.dumps({'families': proposals})
    method._llm = llm
    run(method._make_portfolio(ctx))
    return method, ctx, seed, proposals


def test_every_mechanism_can_update_coupled_callers_without_extra_branches():
    method, ctx, seed, proposals = prepared()
    record = method.portfolios[-1]
    assert len(record['families']) == len(proposals) == 2
    for index, strategy in enumerate(record['families']):
        op = method.operators[f'round0/family{index}']
        assert op['initial_anchor'] == seed.id
        assert op['initial_regions'] == whole_program_regions(seed.genome.files)
        assert strategy['name'] == proposals[index]['name']
        assert strategy['replacement'].startswith(proposals[index]['replacement'])
        assert 'reachable behavior' in strategy['replacement']
        assert strategy['coupled_dependencies_open']


def test_new_helper_and_changed_driver_can_materialize_together():
    method, ctx, seed, _ = prepared()
    masks = method.operators['round0/family0']['initial_regions']
    # The helper and its caller lie in different original semantic components.
    # Both changes must survive materialization while immutable markers survive.
    changes = []
    for i, mask in enumerate(masks):
        path = mask['path']
        after = ('int new_prepare(){return 7;}\nint solve(){return new_prepare();}\nint main(){return solve();}'
                 if path == 'solution.cpp' else 'unchanged\n')
        changes.append({'region': i, 'after': after})
    files, _ = apply_replacements(seed.genome.files, masks, changes, 3)
    assert 'int solve(){return new_prepare();}' in files['solution.cpp']
    assert files['solution.cpp'].startswith('# EVOLVE-BLOCK-START\n')
    assert files['solution.cpp'].endswith('// EVOLVE-BLOCK-END\n')
    assert seed.genome.files['solution.cpp'] == CODE


def test_coupled_masks_and_strategy_survive_checkpoint_roundtrip():
    method, ctx, seed, _ = prepared()
    clone = Molt2LNSCoupled()
    clone.load_state_dict(method.state_dict())
    assert clone.operators['round0/family0'] == method.operators['round0/family0']
    assert clone.portfolios == method.portfolios
