"""Unlaunched raw-control / exact-edit experimenter; no semantic-isolation claim.

One model call proposes both a question and a bounded source edit. Control bytes
are the actual anchor. There is no second model call rewriting a shared template.
Native Molt planning/probation and Scout ownership/priority gates are inherited.
"""
import asyncio,copy,hashlib,json
from dataclasses import replace
from .molt import _json
from .molt2 import source_sha,usable_score
from .molt2_scout import Molt2Scout
from .molt2_discovery import MoltDiscoveryVariator
from ..core.genome import CodeGenome
from ..core.work import Produced


def exact_edit(files,edit):
    """One unique bounded contiguous substitution; all other file bytes retained."""
    if not isinstance(files,dict) or not isinstance(edit,dict):raise ValueError('Files/edit mapping required')
    if set(edit)!={'path','before','after'}:raise ValueError('Exactly path/before/after required')
    path,before,after=(edit[k] for k in ('path','before','after'))
    if not all(isinstance(x,str) for x in (path,before,after)) or path not in files or not isinstance(files[path],str):raise ValueError('Existing text file required')
    limit=min(4096,max(32,len(files[path].encode())//2))
    if not before or before==after or max(len(before.encode()),len(after.encode()))>limit:raise ValueError('Nonempty localized edit required (4096-byte ceiling; half-source limit with32-byte floor)')
    if before.strip()==files[path].strip():raise ValueError('Whole-file replacement is not a localized intervention')
    start=files[path].find(before)
    if start<0 or files[path].find(before,start+1)>=0:raise ValueError('Before text must occur exactly once, including overlapping matches; include exact context')
    control=copy.deepcopy(files);treatment=copy.deepcopy(files);treatment[path]=files[path].replace(before,after,1)
    return control,treatment


class MoltPatchVariator(MoltDiscoveryVariator):
    async def create(self,ctx,item):
        if not item.meta.get('molt_patch_pair'):return await super().create(ctx,item)
        owner=self.discovery_owner;probe=owner.probes.get(item.batch_id)
        if probe is None:return []
        anchor=ctx.store.get(probe['context']['anchor_id'])
        if anchor is None or source_sha(anchor.genome.files)!=probe['context']['anchor_source_sha256'] or owner.fingerprint!=probe['context']['evaluation_fingerprint'] or hashlib.sha256(ctx.objective.encode()).hexdigest()!=probe['context']['objective_sha256'] or not owner._targets_owner_branch(ctx,anchor,probe['owner']):return []
        control,treatment=exact_edit(anchor.genome.files,probe['request']['source_edit']);files={'control':control,'treatment':treatment}
        if files!=probe['expected_files']:return []
        return [Produced(item_id=item.id,batch_id=item.batch_id,genome=CodeGenome(files=copy.deepcopy(files[arm])),parent_ids=list(item.parent_ids),anchor_id=item.anchor_id,meta={**item.meta,'patch_arm':arm}) for arm in ('control','treatment')]


class Molt2Patch(Molt2Scout):
    name='molt2_patch'
    variator_cls=MoltPatchVariator

    def _full_score(self,ind):
        if ind is None:return None
        for m in ind.measurements:
            if m.fidelity=='full' and m.metrics.get('validity')==1:
                score=usable_score(m,self.score_key)
                if score is not None:return score
        return None


    def _question_prompt(self,ctx,anchor,owner):
        text=super()._question_prompt(ctx,anchor,owner)
        text=text.replace('"template_model_calls":1','"template_model_calls":0')
        return text+'\nRAW-CONTROL SOURCE-EDIT PROTOCOL:\nThe control is the exact supplied anchor, never a model rewrite. In experiment also return source_edit={"path":"an existing filename","before":"unique exact source text","after":"replacement source text"}. Each before/after is at most min(4096,max(32,half the source UTF-8 byte length)); before must occur once and cannot cover the whole file. Include context when necessary. One contiguous substitution in one existing file only. Propose a bounded implementation alternative whose effect is meaningful as THIS EXACT SOURCE EDIT. Do not claim a single abstract component was isolated just because a patch is contiguous. No full-program rewrite, new file or second template call. You may decline.\n'

    async def _question(self,ctx,anchor,owner):
        index=owner.get('intervention')
        if self.scout_questions>=2 or index in self.scouted_interventions or self.probe_calls>=self.max_probe_calls:return False
        if not self._targets_owner_branch(ctx,anchor,owner) or not self._eligible_anchor(anchor.id) or self._full_score(anchor) is None:return False
        if not self.reserve_probe_call():return False
        self.scout_questions+=1;self.scouted_interventions.add(index)
        prompt=self._question_prompt(ctx,anchor,owner);digest=source_sha(anchor.genome.files)
        row={'owner':copy.deepcopy(owner),'anchor_id':anchor.id,'anchor_source_sha256':digest,'evaluation_fingerprint':self.fingerprint,'objective_sha256':hashlib.sha256(ctx.objective.encode()).hexdigest(),'prompt':prompt,'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest(),'status':'pending','raw_reply':None,'additional_template_calls':0};self.scout_audit.append(row);before=self._usage_cost()
        try:reply=await self._experiment_llm(prompt)
        except asyncio.CancelledError:row['status']='cancelled';raise
        except Exception as exc:row.update(status='model_error',error_type=type(exc).__name__);return False
        finally:self.probe_cost_upper_bound+=max(0.,self._usage_cost()-before)
        row['raw_reply']=reply;parsed=_json(reply)
        if not isinstance(parsed,dict) or 'experiment' not in parsed:row['status']='malformed';return False
        request=parsed['experiment']
        if request is None:row.update(status='declined',reason=parsed.get('reason'));return False
        fields=('anchor_id','mechanism','decision_to_inform','predicted_contrast','falsifier')
        cost=request.get('expected_cost') if isinstance(request,dict) else None
        if not isinstance(request,dict) or any(not isinstance(request.get(k),str) or not request[k].strip() for k in fields) or request['anchor_id']!=anchor.id or cost!={'candidate_evaluations':2,'template_model_calls':0} or any(type(v) is not int for v in cost.values()):row['status']='malformed';return False
        try:exact_edit(anchor.genome.files,request.get('source_edit'))
        except ValueError as exc:row.update(status='invalid_source_edit',error=str(exc));return False
        if source_sha(anchor.genome.files)!=digest or self.fingerprint!=row['evaluation_fingerprint'] or hashlib.sha256(ctx.objective.encode()).hexdigest()!=row['objective_sha256'] or not self._targets_owner_branch(ctx,anchor,owner):row['status']='stale_context';return False
        accepted=self.queue_evidence_request(ctx,request,owner);row['status']='queued' if accepted else 'rejected_context';return accepted

    def queue_evidence_request(self,ctx,request,owner):
        anchor=ctx.store.get(request.get('anchor_id'))
        try:exact_edit(anchor.genome.files if anchor else {},request.get('source_edit'))
        except ValueError:return False
        return super().queue_evidence_request(ctx,request,owner)

    def _probe_item(self,ctx):
        # Reuse all original native-priority, source-lineage and frontier checks.
        item=super()._probe_item(ctx)
        if item is None:return None
        probe=self.probes[item.batch_id];anchor=ctx.store.get(item.anchor_id)
        control,treatment=exact_edit(anchor.genome.files,probe['request']['source_edit'])
        probe['expected_files']={'control':control,'treatment':treatment}
        item.meta['molt_patch_pair']=True
        return item

    async def on_measured(self,ctx,ind,measurement):
        probe=self.probes.get(measurement.batch_id)
        if probe is None:return await super().on_measured(ctx,ind,measurement)
        m=measurement
        if m.fidelity!='full' or m.metrics.get('validity')!=1:m=replace(m,ok=False,artifacts={**(m.artifacts or {}),'patch_diagnostic_issue':'Incomplete full-valid measurement; no effect claim'})
        arm=next((arm for arm,files in probe['expected_files'].items() if ind.genome.files==files),None)
        if arm in probe['arms'] or (arm is None and ind.id in probe['unknown_ids']):return
        if arm is None:probe['unknown_ids'].append(ind.id);probe['failures'].append({'id':ind.id,'reason':'source_does_not_match_exact_planned_arm'})
        else:probe['arms'][arm]={'id':ind.id,'score':usable_score(m,self.score_key),'files':dict(ind.genome.files),'source_sha256':source_sha(ind.genome.files),'measurement':m.__dict__.copy()}
        probe['resolved']+=1
        if probe['resolved']>=2:self._finish_probe(ctx,m.batch_id)

    def _finish_probe(self,ctx,batch):
        probe=self.probes.pop(batch,None)
        if probe is None:return
        self.closed_probes.add(batch);a=probe['arms'].get('control',{});b=probe['arms'].get('treatment',{});sa,sb=a.get('score'),b.get('score')
        anchor=ctx.store.get(probe['context']['anchor_id']);context_ok=anchor is not None and source_sha(anchor.genome.files)==probe['context']['anchor_source_sha256'] and probe['context']['evaluation_fingerprint']==self.fingerprint and probe['context']['objective_sha256']==hashlib.sha256(ctx.objective.encode()).hexdigest()
        exact=bool(a.get('files')==probe['expected_files']['control'] and b.get('files')==probe['expected_files']['treatment'])
        delta=sb-sa if exact and context_ok and sa is not None and sb is not None else None
        reproduced=sa is not None and abs(sa-probe['anchor_score'])<=self.control_tolerance*max(abs(probe['anchor_score']),1e-12)
        status='inconclusive' if delta is None else ('confounded_control' if not reproduced else 'positive_observed_pair' if delta>0 else 'negative_observed_pair' if delta<0 else 'no_observed_effect')
        row={k:copy.deepcopy(v) for k,v in probe.items() if k not in ('arms','expected_files')}
        row.update(status=status,delta=delta,anchor_reproduced=reproduced,raw_anchor_control_exact=bool(a.get('files')==probe['expected_files']['control']),exact_source_edit=exact,one_switch_source_contrast=False,pair_source_sha256=source_sha(probe['expected_files']),hypotheses={'HYPOTHESIS':probe['request']['predicted_contrast'],'FALSIFIER':probe['request']['falsifier'],'INTERVENTION_UNIT':'This exact source edit; not universal evidence about an abstract component.','EXACT_SOURCE_EDIT':json.dumps(probe['request']['source_edit'],ensure_ascii=False)},states={arm:{'individual_id':v['id'],'score':v['score'],'source_sha256':v['source_sha256']} for arm,v in probe['arms'].items()},executable_states={arm:v['files'] for arm,v in probe['arms'].items()},planned_executable_states=copy.deepcopy(probe['expected_files']),measurements={arm:v['measurement'] for arm,v in probe['arms'].items()},errors=copy.deepcopy(probe['failures']),scope='One unreplicated raw-control/exact-edit comparison; syntactic localization does not prove conceptual isolation or generality.')
        for arm,value in probe['arms'].items():
            if value['score'] is None:
                m=value['measurement'];row['errors'].append({'arm':arm,'validity':m.get('metrics',{}).get('validity'),'fidelity':m.get('fidelity'),'reason':m.get('artifacts',{}).get('evaluation_error') or m.get('artifacts',{}).get('patch_diagnostic_issue') or 'Missing/unusable measurement'})
        self.evidence.append(row)
        # Never insert another clone of the unchanged anchor. Only a positive,
        # fully valid treatment beating both observed control and original reading.
        live_chains=[c for c in probe['context']['chains'] if not self._retired(c) and anchor is not None and anchor.id in self.chains[c]]
        if status=='positive_observed_pair' and sb>=probe['anchor_score'] and context_ok and live_chains and self._eligible_anchor(anchor.id) and self._targets_owner_branch(ctx,anchor,probe['owner']):
            for chain in live_chains:self.chains[chain].append(b['id'])
            if self.anchor is None or sb>self.anchor+self.progress_rel*abs(self.anchor):self.anchor,self.since=sb,0

    def state_dict(self):
        state=super().state_dict();state['patch_method']={'schema':1};return state

    def load_state_dict(self,state):
        if state.get('patch_method',{}).get('schema',1)!=1:raise ValueError('Unsupported patch checkpoint')
        old=state.get('discovery',{})
        if 'patch_method' not in state and (old.get('requests') or old.get('probes') or old.get('evidence') or state.get('scout',{}).get('questions')):raise ValueError('Cannot import template-based experiment history as raw-control evidence')
        if any('expected_files' not in p for p in state.get('discovery',{}).get('probes',{}).values()):raise ValueError('Cannot resume template-based probes as exact edits')
        super().load_state_dict(state)
