import unittest
from budget_selection import eligible_store

def row(identity,score,at,code=None,valid=1):
 return {'id':identity,'kind':'code','genome':{'_type':'code','files':{'solution.cpp':code or identity}},'measurements':[{'ok':True,'fidelity':'full','at':at,'metrics':{'combined_score':score,'validity':valid}}]}
class BudgetSelectionTests(unittest.TestCase):
 def test_batch_callbacks_cannot_admit81(self):
  summary={'eval_usage':{'calls':82},'history':[{'t':300,'score':2},{'t':300,'score':99}],'usage_timeline':[{'kind':'eval','t':100,'eval_calls':1},{'kind':'eval','t':200,'eval_calls':80},{'kind':'eval','t':201,'eval_calls':81}]}
  result,audit=eligible_store({'individuals':[row('seed',1,100.00002),row('off',2,200.00002),row('on',99,201.00002),row('invalid',None,200,valid=0)]},summary)
  self.assertEqual([r['id'] for r in result['individuals']],['seed','off']);self.assertEqual(audit['selection_cutoff'],80)
 def test_first_valid_distinct_code_not_lucky_reread(self):
  summary={'eval_usage':{'calls':20},'usage_timeline':[{'kind':'eval','t':100,'eval_calls':1},{'kind':'eval','t':101,'eval_calls':2}]}
  result,audit=eligible_store({'individuals':[row('first',2,100,code='same'),row('reread',99,101,code='same')]},summary)
  self.assertEqual(len(result['individuals']),1);self.assertEqual(result['individuals'][0]['id'],'first');self.assertEqual(audit['selection_cutoff'],20)
 def test_missing_score_and_unmatched_time_ineligible(self):
  summary={'eval_usage':{'calls':80},'usage_timeline':[{'kind':'eval','t':100,'eval_calls':1}]}
  result,_=eligible_store({'individuals':[row('missing',None,100),row('unmatched',10,110)]},summary)
  self.assertEqual(result['individuals'],[])
if __name__=='__main__':unittest.main()
