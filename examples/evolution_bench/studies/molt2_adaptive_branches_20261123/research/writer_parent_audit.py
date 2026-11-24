"""Audit actual emitted redesign targets from saved method state; no scoring."""
from pathlib import Path
import json,collections,hashlib,time
R=Path(__file__).resolve().parents[1]
def build():
 rows=[]
 for run in sorted((R/'runs').iterdir()):
  if not (run/'method.json').exists() or not (run/'store.json').exists():continue
  method=json.loads((run/'method.json').read_text())['state']
  if 'modified_molt_writer' not in method:continue
  inds={i['id']:i for i in json.loads((run/'store.json').read_text())['individuals']};targets=[]
  for entry in method['modified_molt_writer'].get('target_context_audit',[]):
   ind=inds.get(entry['target']);item={'direction':entry['direction'],'target':entry['target'],'target_found':ind is not None}
   if ind:
    code=ind['genome']['files'][entry['path']];ms=ind.get('measurements',[]);metrics=ms[0]['metrics'] if ms else {}
    item.update(generation=ind.get('generation'),has_parents=bool(ind.get('parent_ids')),training_score=metrics.get('combined_score'),source_hash_matches=hashlib.sha256(code.encode()).hexdigest()==entry['source_sha256'])
   targets.append(item)
  rows.append({'run':run.name,'emitted_redesign_directions':len(targets),'generation_zero_targets':sum(t.get('generation')==0 for t in targets),'evolved_targets':sum(t.get('has_parents',False) for t in targets),'unique_targets':len({t['target'] for t in targets}),'targets':targets})
 return {'at_unix':time.time(),'rows':rows,'scope':'Only emitted redesign directions in modified/adaptive method audits. Native planner target choice was preserved. Distinct directions can share one target. An evolved-target benefit cannot be inferred from seed-target restarts.'}
if __name__=='__main__':
 result=build();(R/'research/WRITER_PARENT_AUDIT.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
