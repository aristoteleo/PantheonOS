"""Read-only development trace snapshot; does not influence ongoing searches."""
from pathlib import Path
import json,time,collections
R=Path(__file__).resolve().parents[1]
rows=[]
for run in sorted((R/'runs').iterdir()):
 if not (run/'partial_summary.json').exists():continue
 summary_path=run/'summary.json' if (run/'summary.json').exists() else run/'partial_summary.json'
 summary=json.loads(summary_path.read_text());history=summary.get('history',[])
 state=json.loads((run/'method.json').read_text())['state'] if (run/'method.json').exists() else {}
 adaptive=state.get('adaptive_branches',{});alloc=adaptive.get('history',[])
 rows.append({'run':run.name,'evaluated_history':len(history),'best_evaluated_candidate':max((x['score'] for x in history),default=None),'invalid_candidates':sum(x.get('valid',0)<=0 for x in history),'allocation_evaluations_by_arm':dict(collections.Counter({arm:sum(x['evaluations'] for x in alloc if x['arm']==arm) for arm in {x['arm'] for x in alloc}})),'allocation_meaningful_improvements':sum(x['success'] for x in alloc),'dispatch_reason_counts':dict(collections.Counter(x['reason'] for x in adaptive.get('decisions',[]))),'incomplete':not (run/'summary.json').exists()})
result={'at_unix':time.time(),'rows':rows,'caution':'Running partial results; best candidate excludes separately measured starting seed. Reward requires native meaningful-progress threshold, so tiny numeric changes need not earn reward. No finished cohort comparison yet.'}
(R/'research/LIVE_DIAGNOSTIC.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
