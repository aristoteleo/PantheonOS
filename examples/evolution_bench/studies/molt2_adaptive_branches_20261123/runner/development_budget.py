"""Public development scores restricted to eligible first80 search measurements."""
from pathlib import Path
import json,math
from budget_selection import eligible_store

def best_eligible_development(directory):
 directory=Path(directory);summary=None
 for name in ['summary.json','partial_summary.json']:
  try:
   value=json.loads((directory/name).read_text())
   if isinstance(value,dict) and isinstance(value.get('usage_timeline'),list) and value.get('eval_usage') is not None:summary=value;break
  except (OSError,ValueError):pass
 if summary is None:return None
 try:store=json.loads((directory/'store.json').read_text())
 except (OSError,ValueError):return None
 eligible,_=eligible_store(store,summary,80);scores=[]
 for row in eligible['individuals']:
  score=row['measurements'][0]['metrics']['combined_score']
  if isinstance(score,(int,float)) and not isinstance(score,bool) and math.isfinite(score):scores.append(score)
 return max(scores) if scores else None
