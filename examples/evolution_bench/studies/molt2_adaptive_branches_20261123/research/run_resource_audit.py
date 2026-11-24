"""Separate confirmed provider cost, unknown holds, search time and receipt tail."""
from pathlib import Path
import json,time
R=Path(__file__).resolve().parents[1]
def build():
 cfg=json.loads((R/'campaign.json').read_text());ledger=json.loads((Path(cfg['shared_ledger_root'])/'spend-ledger.json').read_text());rows=[]
 for p in sorted((R/'runs').glob('*/workflow.json')):
  run=p.parent;state=json.loads(p.read_text());calls=[v for v in ledger['calls'].values() if v.get('run_id')==run.name];summary=json.loads((run/'summary.json').read_text()) if (run/'summary.json').exists() else {}
  eval_events=[e for e in summary.get('usage_timeline',[]) if e.get('kind')=='eval' and 0<e.get('eval_calls',0)-e.get('eval_calls_harness',0)<=80]
  cutoff_events=[e for e in eval_events if e.get('eval_calls',0)-e.get('eval_calls_harness',0)==80]
  cutoff_seconds=max(e['t'] for e in cutoff_events)-summary['t0_epoch'] if cutoff_events else None
  protocol=json.loads((run/'RECEIPT_PROTOCOL.json').read_text()) if (run/'RECEIPT_PROTOCOL.json').exists() else None
  rows.append({'run':run.name,'status':state['status'],'confirmed_provider_usd':sum(v['cost_usd'] or 0 for v in calls),'unknown_request_count':sum(v['cost_usd'] is None for v in calls),'conservative_total_usd':sum(v['conservative_charge_usd'] for v in calls),'native_reported_seconds':summary.get('seconds'),'time_to_80th_recorded_evaluation_seconds':cutoff_seconds,'process_wall_seconds':state.get('wall_seconds'),'receipt_protocol':protocol})
 return {'at_unix':time.time(),'rows':rows,'caution':'Unknown charges remain booked. Confirmed provider totals exclude unknown requests and are not complete billed totals where unknown_count>0. Process wall time and native reported seconds may include receipt waits during worker cleanup. Time to80th recorded evaluation uses actual search-event timestamps and excludes later receipt-only tail; it is withheld if80 calls were not reached.'}
if __name__=='__main__':
 result=build();(R/'research/RUN_RESOURCE_AUDIT.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
