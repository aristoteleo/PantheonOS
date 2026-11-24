"""Eligibility audit for the documented legacy Molt2 event-identity defect."""
from pathlib import Path
import json,collections,time
R=Path(__file__).resolve().parents[1]
def classify(individuals,legacy):
 repeated=[];same_batch=[];varying=[]
 for ind in individuals:
  ms=[m for m in ind.get('measurements',[]) if m.get('ok',True)]
  if len(ms)<2:continue
  repeated.append(ind['id']);groups=collections.Counter(m.get('batch_id') for m in ms)
  if any(batch and n>1 for batch,n in groups.items()):same_batch.append(ind['id'])
  scores={m.get('metrics',{}).get('combined_score') for m in ms}
  if len(scores)>1:varying.append(ind['id'])
 reasons=[]
 if legacy and same_batch:reasons.append('Distinct measurements of one genome in one batch can be collapsed')
 if legacy and varying:reasons.append('Repeated-source score changes require review of legacy latest-score credit')
 return {'repeated_program_ids':repeated,'same_batch_repeated_program_ids':same_batch,'varying_score_program_ids':varying,'legacy_event_review_required':bool(reasons),'reasons':reasons}
def build():
 amendment=json.loads((R/'research/measurement-event-fix/AMENDMENT.json').read_text());old={x['run'] for x in amendment['already_started_trials']};rows=[];excluded=[]
 for job in json.loads((R/'manifest.json').read_text()):
  if job['method']!='molt2_adaptive_branches':continue
  p=R/'runs'/job['id']
  if not (p/'store.json').exists():continue
  provenance=json.loads((p/'ENGINE_PROVENANCE.json').read_text()) if (p/'ENGINE_PROVENANCE.json').exists() else {}
  commit=provenance.get('engine_commit')
  legacy=commit==amendment['old_engine_commit'] or (not commit and job['id'] in old)
  fixed=commit==amendment['new_engine_commit']
  state=json.loads((p/'workflow.json').read_text());audit=classify(json.loads((p/'store.json').read_text())['individuals'],legacy)
  review=audit['legacy_event_review_required'] or not (legacy or fixed)
  row={'run':job['id'],'workflow_status':state['status'],'engine_classification':'legacy' if legacy else 'fixed' if fixed else 'unknown','review_required':review,**audit}
  if state['status']=='complete' and review:excluded.append(job['id'])
  rows.append(row)
 return {'at_unix':time.time(),'rows':rows,'excluded_primary_run_ids':excluded,'policy':'Score-independent implementation-integrity gate. Keep raw results, withhold affected/ambiguous legacy runs pending documented review or correction. No exclusion based on how well the program scored.'}
if __name__=='__main__':
 result=build();(R/'research/MEASUREMENT_EVENT_AUDIT.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
