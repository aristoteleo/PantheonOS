"""Read-only starting-source and selected-source audit; no scoring/model calls."""
from pathlib import Path
import json,hashlib,time
R=Path(__file__).resolve().parents[1]
def build():
 jobs=json.loads((R/'manifest.json').read_text());rows=[]
 for job in jobs:
  p=R/'runs'/job['id'];store=p/'store.json'
  if not store.exists():continue
  inds=json.loads(store.read_text())['individuals'];seed=next((i for i in inds if i['kind']=='code' and not i['parent_ids'] and i.get('measurements')),None)
  if seed is None:continue
  digest=hashlib.sha256(seed['genome']['files']['solution.cpp'].encode()).hexdigest();expected=hashlib.sha256((R/'tasks'/job['task']/'solution.cpp').read_bytes()).hexdigest();m=seed['measurements'][0]['metrics']
  row={'run':job['id'],'task':job['task'],'starting_source_sha256':digest,'matches_fixed_seed':digest==expected,'starting_score':m.get('combined_score'),'starting_validity':m.get('validity'),'starting_cases_accepted':m.get('num_accepted')}
  if (p/'selection.json').exists():
   selected=json.loads((p/'selection.json').read_text());ind=next(i for i in inds if i['id']==selected['id']);row.update(selected_generation=ind['generation'],selected_is_starting_source=selected['sha256']==digest,selected_score=selected['score'])
  rows.append(row)
 return {'at_unix':time.time(),'observed_starts':len(rows),'scheduled_trials':len(jobs),'all_observed_sources_match':bool(rows) and all(x['matches_fixed_seed'] for x in rows),'all_observed_seeds_valid150':bool(rows) and all(x['starting_validity']==1 and x['starting_cases_accepted']==150 for x in rows),'rows':rows,'caution':'Source parity is distinct from identical stochastic histories. Wall-clock scores and prompt timing metrics can differ even with the same initial source and configured algorithm/model seeds.'}
if __name__=='__main__':
 result=build();(R/'research/BASELINE_PARITY.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
