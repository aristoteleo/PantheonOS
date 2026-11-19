import pytest
from pantheon.evolution.methods.molt2_lns_global import whole_program_regions
from pantheon.evolution.methods.molt2_lns import apply_replacements


def test_whole_program_rewrite_preserves_markers_and_opens_all_other_lines():
    files={'solution.cpp':'// EVOLVE-BLOCK-START\nint helper(){return 1;}\nint main(){return helper();}\n// EVOLVE-BLOCK-END\n'}
    masks=whole_program_regions(files)
    result,_=apply_replacements(files,masks,[{'region':0,'after':'int main(){return 0;}'}],3)
    assert result['solution.cpp']=='// EVOLVE-BLOCK-START\nint main(){return 0;}\n// EVOLVE-BLOCK-END\n'


def test_all_files_included_and_fragmentation_never_silently_truncated():
    assert len(whole_program_regions({'a.cpp':'a\n','b.cpp':'b\n'}))==2
    with pytest.raises(ValueError):
        whole_program_regions({str(i):'a\n' for i in range(13)})


def test_marker_only_program_is_rejected_without_model_spending():
    with pytest.raises(ValueError):whole_program_regions({'a':'EVOLVE-BLOCK-START\nEVOLVE-BLOCK-END\n'})


def test_global_operator_inherits_best_source_and_opens_all_nonmarker_regions():
    import json
    from test_molt2_lns import make, family, run
    from pantheon.evolution.methods.molt2_lns_global import Molt2LNSGlobal
    method, ctx, seed = make()
    method.__class__ = Molt2LNSGlobal
    async def llm(prompt):
        return json.dumps({'families':[family('a',2),family('b',3),family('c',4)]})
    method._llm = llm
    run(method._make_portfolio(ctx))
    portfolio = method.portfolios[-1]
    arm = portfolio['explicit_global_arm']
    operator = method.operators[arm]
    assert portfolio['scope'] == operator['scope'] == 3
    assert operator['initial_anchor'] == seed.id
    assert operator['strategy']['name'] == 'coordinated_algorithm_rewrite'
    assert operator['initial_regions'] == whole_program_regions(seed.genome.files)
    assert len(portfolio['families']) == 3
