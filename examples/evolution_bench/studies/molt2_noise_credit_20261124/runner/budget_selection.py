"""Reporting/selection ledger join; never changes optimizer execution or scoring."""
import copy,hashlib,math

def eligible_store(store,summary,budget=80,required_cases=None):
 usage=summary.get('eval_usage') or {};actual=usage.get('calls',0)-usage.get('calls_harness',0);cutoff=min(budget,actual)
 events=[];previous_low=0
 for event in summary.get('usage_timeline',[]):
  if event.get('kind')!='eval':continue
  low=event.get('eval_calls_low',0)
  if low<=previous_low:events.append(event)
  previous_low=low
 records=[];excluded=[]
 for row in store.get('individuals',[]):
  genome=row.get('genome') or {};code=(genome.get('files') or {}).get('solution.cpp')
  if row.get('kind',genome.get('kind'))!='code' or not isinstance(code,str):continue
  first=None
  for measurement in row.get('measurements',[]):
   metrics=measurement.get('metrics') or {};score=metrics.get('combined_score');valid=metrics.get('validity',1)
   if required_cases is not None and (valid!=1 or metrics.get('num_cases')!=required_cases or metrics.get('num_accepted')!=required_cases):continue
   if measurement.get('ok',True) and measurement.get('fidelity','full')=='full' and isinstance(score,(int,float)) and math.isfinite(score) and isinstance(valid,(int,float)) and math.isfinite(valid) and valid>0:
    first=measurement;break
  if first is None:continue
  at=first.get('at');event=min(events,key=lambda e:abs(e['t']-at)) if events and isinstance(at,(int,float)) and math.isfinite(at) else None
  index=event['eval_calls']-event.get('eval_calls_harness',0) if event is not None and abs(event['t']-at)<.1 else None
  digest=hashlib.sha256(code.encode()).hexdigest()
  record={'individual_id':row['id'],'code_sha256':digest,'measurement_at':at,'search_eval_index':index,'score':first['metrics']['combined_score']}
  records.append((at if isinstance(at,(int,float)) else float('inf'),row,first,record))
 # First valid full reading of each distinct code, including seed. A later reread cannot upgrade it.
 records.sort(key=lambda x:x[0]);seen=set();eligible=[];audit=[]
 for _,row,first,record in records:
  if record['code_sha256'] in seen:continue
  seen.add(record['code_sha256']);index=record['search_eval_index'];record['eligible']=index is not None and 0<index<=cutoff;audit.append(record)
  if record['eligible']:
   saved=copy.deepcopy(row);saved['measurements']=[copy.deepcopy(first)];eligible.append(saved)
 result=dict(store);result['individuals']=eligible
 return result,{'budget_limit':budget,'actual_search_eval_calls':actual,'selection_cutoff':cutoff,'eligible_distinct_codes':len(eligible),'first_valid_code_readings':audit,'join_tolerance_seconds_strict':.1}
