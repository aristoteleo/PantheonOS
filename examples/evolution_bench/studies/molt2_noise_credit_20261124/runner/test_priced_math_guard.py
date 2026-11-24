import copy
from pathlib import Path
import pytest
from priced_math_guard import PricedMathGuard
from model_io_queue import AdmissionDenied

def make(tmp_path):
    return PricedMathGuard(caller_lab=tmp_path/'caller',ledger_root=tmp_path/'shared',
                           io_dir=tmp_path/'io',provider='DeepInfra',limit=74,model_seed=400)

def request():
    return {'model':'deepseek/deepseek-v4.1-flash','extra_body':{'provider':{'order':['DeepInfra'],'allow_fallbacks':False}}}

def test_price_caps_and_model_seeds_are_set_before_admission(tmp_path):
    g=make(tmp_path);a=request();b=request();g.validate(a);g.validate(b)
    assert a['seed']==400000 and b['seed']==400001
    assert a['extra_body']['provider']['max_price']=={'prompt':.151,'completion':.451}
    assert a['extra_body']['provider']['allow_fallbacks'] is False
    assert not g.ledger.exists()

def test_wrong_model_is_rejected_without_a_request(tmp_path):
    g=make(tmp_path);a=request();a['model']='different'
    with pytest.raises(AdmissionDenied):g.validate(a)
    assert g.stopped() and not g.ledger.exists() and not list(g.root.glob('*.request.json'))

def test_different_provider_is_rejected_before_charge(tmp_path):
    g=make(tmp_path);a=request();a['extra_body']['provider']['order']=['Other']
    with pytest.raises(AdmissionDenied):g.validate(a)
    assert g.stopped() and not g.ledger.exists()
