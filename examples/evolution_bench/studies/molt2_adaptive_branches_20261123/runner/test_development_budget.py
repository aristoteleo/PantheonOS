import json,tempfile,unittest
from pathlib import Path
from development_budget import best_eligible_development

def row(i,score,at):return {'id':str(i),'kind':'code','genome':{'files':{'solution.cpp':str(i)}},'measurements':[{'ok':True,'fidelity':'full','at':at,'metrics':{'combined_score':score,'validity':1}}]}
class DevelopmentBudgetTests(unittest.TestCase):
 def write(self,d,calls):
  (d/'summary.json').write_text(json.dumps({'best_combined_score':999,'eval_usage':{'calls':calls},'usage_timeline':[{'kind':'eval','t':100,'eval_calls':1},{'kind':'eval','t':200,'eval_calls':min(calls,80)},{'kind':'eval','t':201,'eval_calls':81}]}))
  (d/'store.json').write_text(json.dumps({'individuals':[row(1,1,100.00002),row(2,2,200.00002),row(3,999,201.00002)]}))
 def test_eval81_high_score_excluded(self):
  with tempfile.TemporaryDirectory() as tmp:
   d=Path(tmp);self.write(d,82);self.assertEqual(best_eligible_development(d),2)
 def test_completed_less_than80_allowed(self):
  with tempfile.TemporaryDirectory() as tmp:
   d=Path(tmp);self.write(d,12);self.assertEqual(best_eligible_development(d),2)
 def test_missing_ledger_never_raw_fallback(self):
  with tempfile.TemporaryDirectory() as tmp:
   d=Path(tmp);(d/'summary.json').write_text(json.dumps({'best_combined_score':999}));self.assertIsNone(best_eligible_development(d))
if __name__=='__main__':unittest.main()
