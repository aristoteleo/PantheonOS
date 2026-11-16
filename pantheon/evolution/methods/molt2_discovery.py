"""Native Molt architecture discovery with optional, context-scoped paired probes.

No new architecture family is forced and no heldout scores enter this controller.
Candidate-slot reservations are hard; observed-dollar probe thresholds are soft
unless the caller supplies a stronger provider reservation service. All model
calls use the ordinary SDK path and its caller-owned global reservation guard.
"""
from __future__ import annotations
import asyncio,contextvars,copy,hashlib,json,os,re,threading
from dataclasses import replace
from .molt import Molt,MoltVariator,SPROUT,BASE,ESTABLISHED,_json
from .molt2 import toggle_pair,switch_value,source_sha,usable_score,first_score,PAIR_INSTRUCTION
from .molt2_factorial import hypotheses,Molt2Factorial
from ..core.genome import CodeGenome
from ..core.work import Create,Produced,PromptContext,Measurement

PROBE=contextvars.ContextVar('molt_discovery_probe',default=False)
REQUEST_INSTRUCTION='''\nOPTIONAL MEASUREMENT INSTRUMENT (not a fourth direction type):
Keep the same integration/exploration/redesign portfolio and its native priorities.
For a decision genuinely requiring a component contrast, a direction may additionally
include evidence_request={anchor_id,mechanism,decision_to_inform,predicted_contrast,
falsifier,expected_cost}. anchor_id must name a live program. An experiment is not
mandatory for architecture changes. Requests consume a small shared search budget.
Predictions and falsifiers may be multiline. Negative observations apply only to
recorded anchor/context; do not ban an entire algorithm family based on one result.
'''

def audited_pair(files,path):
    code=files[path]
    if not path.endswith('.py'):
        logical=re.sub(r'\\\r?\n','',code)
        lexical=re.sub(r'//[^\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', ' ',logical)
        if re.search(r'^[ \t]*(?:#|%:)[^\n]*\bMOLT2_TREATMENT\b',lexical,re.M):raise ValueError('Probe flag cannot be a preprocessor condition')
    return toggle_pair(files,path)

class CandidateSlots:
    """Process-local atomic reservations; pending work counts before dispatch."""
    def __init__(self,total=80,diagnostic=8):
        self.total=int(total);self.diagnostic=min(8,max(0,int(diagnostic)));self.native=0;self.probe=0;self.lock=threading.Lock()
    def reserve(self,n,probe=False):
        with self.lock:
            available=max(0,self.total-self.native-self.probe)
            if probe:
                if n!=2 or available<n or self.probe+n>self.diagnostic:return 0
                self.probe+=n;return n
            n=min(int(n),available);self.native+=n;return n
    def remaining(self):return max(0,self.total-self.native-self.probe)
    def state(self):return dict(total=self.total,diagnostic=self.diagnostic,native=self.native,probe=self.probe)

class MoltDiscoveryVariator(MoltVariator):
    def _augment(self,prompt,item):
        if item.meta.get('molt_discovery_probe'):
            request=copy.deepcopy(item.meta['molt_discovery_request'])
            for key,value in list(request.items()):
                if isinstance(value,str) and len(value)>1500:
                    request[key]={'text':value[:1500]+' [TRUNCATED IN PROMPT; see full request ledger]','original_chars':len(value),'sha256':hashlib.sha256(value.encode()).hexdigest()}
            prompt+='\n\n'+PAIR_INSTRUCTION+'\nDecision-linked request:\n'+json.dumps(request,ensure_ascii=False)
        memory=item.context.extra.get('discovery_memory','')
        return prompt+('\n\nCONTEXT-SCOPED OBSERVED EVIDENCE (not universal laws):\n'+memory if memory else '')
    def build_block_prompt(self,ctx,item,path,eb):return self._augment(super().build_block_prompt(ctx,item,path,eb),item)
    def build_prompt(self,ctx,item,path,parent_code):return self._augment(super().build_prompt(ctx,item,path,parent_code),item)
    async def _complete(self,prompt,k,**kwargs):
        if not PROBE.get():return await super()._complete(prompt,k,**kwargs)
        owner=self.discovery_owner
        if not owner.reserve_probe_call():raise RuntimeError('Diagnostic model-call allowance unavailable')
        before=owner._usage_cost()
        try:return await super()._complete(prompt,k,**kwargs)
        finally:owner.probe_cost_upper_bound+=max(0.,owner._usage_cost()-before)
    async def create(self,ctx,item):
        if not item.meta.get('molt_discovery_probe'):return await super().create(ctx,item)
        token=PROBE.set(True)
        try:templates=await super().create(ctx,replace(item,k=1))
        finally:PROBE.reset(token)
        if not templates:return []
        template=templates[0];path=self._file_to_evolve(template.genome.files)
        try:off,on,digest=audited_pair(template.genome.files,path)
        except (ValueError,KeyError):return []
        return [Produced(item_id=item.id,batch_id=item.batch_id,genome=CodeGenome(files=files),parent_ids=list(item.parent_ids),anchor_id=item.anchor_id,meta={**template.meta,'discovery_arm':arm,'discovery_pair_sha256':digest}) for arm,files in [('control',off),('treatment',on)]]

class Molt2Discovery(Molt):
    name='molt2_discovery'
    variator_cls=MoltDiscoveryVariator
    def __init__(self,*,max_eval_calls=80,diagnostic_slots=8,max_probe_calls=4,probe_usd_threshold=.05,native_usd_headroom=.10,run_usd_cap=.5,control_tolerance=.02,**kwargs):
        super().__init__(**kwargs)
        self.slots=CandidateSlots(max_eval_calls,diagnostic_slots)
        self.requests=[];self.probes={};self.evidence=[];self.closed_probes=set();self.rejected_requests=[]
        self.max_probe_calls=int(max_probe_calls);self.probe_calls=0;self.probe_cost_upper_bound=0.
        self.probe_usd_threshold=float(probe_usd_threshold);self.native_usd_headroom=float(native_usd_headroom);self.run_usd_cap=float(run_usd_cap);self.control_tolerance=float(control_tolerance)
        self.fingerprint=os.environ.get('MOLT2_EVAL_FINGERPRINT','');self._planning_ctx=None;self.native_callbacks=0;self.planner_request_seq=0
    def default_variator(self,**kwargs):
        v=super().default_variator(**kwargs);v.discovery_owner=self;return v
    async def start(self,ctx,seeds):
        await super().start(ctx,seeds);self.slots.reserve(len(seeds))
        if not self.fingerprint:self.fingerprint=hashlib.sha256(ctx.objective.encode()).hexdigest()
    def _usage_cost(self):
        from ..variators.usage import snapshot
        return float(snapshot().get('cost_usd',0.) or 0.)
    def _financial_headroom(self):
        return self.probe_calls<self.max_probe_calls and self.probe_cost_upper_bound<self.probe_usd_threshold and self._usage_cost()+self.probe_usd_threshold+self.native_usd_headroom<=self.run_usd_cap
    def reserve_probe_call(self):
        # No await between check and reservation: atomic inside the native asyncio
        # process. SDK-level global reservations are provided by the caller, exactly
        # as for native planner/worker calls; no guessed provider prices are used.
        if not self._financial_headroom():return False
        self.probe_calls+=1;return True
    def _related(self,ctx,parents,anchor_id):
        todo=list(parents);seen=set()
        while todo and len(seen)<256:
            iid=todo.pop()
            if iid==anchor_id:return True
            if iid in seen:continue
            seen.add(iid);ind=ctx.store.get(iid)
            if ind:
                # Molt deliberately keeps descent across a full architectural
                # redesign. Descent alone is not component applicability.
                redesign_roots={ch[0] for c,ch in enumerate(self.chains) if ch and c in self.sprouts}
                if ind.meta.get('molt_type')=='redesign' or iid in redesign_roots:continue
                todo.extend(ind.parent_ids)
        return False
    def _memory(self,ctx,parent_ids):
        rows=[]
        live=[i for c,ch in enumerate(self.chains) if not self._retired(c) for i in ch]
        for e in self.evidence:
            if e['context']['evaluation_fingerprint']!=self.fingerprint or e['context']['objective_sha256']!=hashlib.sha256(ctx.objective.encode()).hexdigest():continue
            anchor=ctx.store.get(e['context']['anchor_id'])
            if anchor is None or source_sha(anchor.genome.files)!=e['context']['anchor_source_sha256']:continue
            ids=live if parent_ids is None else parent_ids
            if not self._related(ctx,ids,e['context']['anchor_id']):continue
            row=copy.deepcopy(e)
            row['applicability']='Recorded anchor source or its ancestry in this active context; changes of representation require a new contrast. Not universal support/rejection.'
            rows.append(row)
        # Reuse the already-tested bounded prompt-only projection. Raw discovery
        # records keep full sources/comments/per-case measurements independently.
        projection=Molt2Factorial.__new__(Molt2Factorial);projection.evidence=[];projection.memory_size=8
        for e in rows:
            projection.evidence.append({'hypotheses':e.get('hypotheses'), 'status':e['status'],'anchor_id':e['context']['anchor_id'],'anchor_source_sha256':e['context']['anchor_source_sha256'],'anchor_score':e['anchor_score'],'anchor_reproduced':e['anchor_reproduced'],'effects':{'delta':e['delta']},'evaluation_fingerprint':self.fingerprint,'context_sha256':e['context_sha256'],'states':e['states'],'errors':e['errors'],'scope':e['applicability']})
        return Molt2Factorial._memory(projection)
    async def _plan(self,ctx):
        self._planning_ctx=ctx
        already_pending={d.get('id') for d in self.pending}
        try:
            await super()._plan(ctx)
            # Native Molt has now validated live targets, deduplicated directions,
            # and assigned immutable native direction/intervention ownership.
            for d in self.pending:
                if d.get('id') in already_pending:continue
                request=d.get('evidence_request')
                if isinstance(request,dict):
                    self.queue_evidence_request(ctx,request,{'native_direction_id':d['id'],'intervention':d['intervention'],'native_direction_type':d.get('type')})
        finally:self._planning_ctx=None
    async def _llm(self,prompt):
        planning=self._planning_ctx is not None and prompt.startswith('# Dynamic branch intervention portfolio')
        call_id=self.planner_request_seq
        if planning:
            self.planner_request_seq+=1
            prompt+=REQUEST_INSTRUCTION+'\n'+self._memory(self._planning_ctx,None)
        reply=await super()._llm(prompt)
        if planning:
            parsed=_json(reply)
            if not isinstance(parsed,dict):return json.dumps({'directions':[],'diagnosis':'Malformed non-object planner reply; no directions inferred'})
            ds=parsed.get('directions',[])
            if not isinstance(ds,list):
                parsed['directions']=[];return json.dumps(parsed)
        return reply
    def queue_evidence_request(self,ctx,request,owner):
        if any(not isinstance(request.get(k),str) or not request[k].strip() for k in ('anchor_id','mechanism','decision_to_inform','predicted_contrast','falsifier')):return False
        anchor=ctx.store.get(request['anchor_id'])
        chains=[c for c,ch in enumerate(self.chains) if not self._retired(c) and request['anchor_id'] in ch]
        if anchor is None or not isinstance(anchor.genome,CodeGenome) or first_score(anchor,self.score_key) is None or not chains:return False
        context={'anchor_id':anchor.id,'anchor_source_sha256':source_sha(anchor.genome.files),'evaluation_fingerprint':self.fingerprint,'chains':chains,'objective_sha256':hashlib.sha256(ctx.objective.encode()).hexdigest()}
        row={'request':copy.deepcopy(request),'owner':copy.deepcopy(owner),'context':context,'mechanism_id':hashlib.sha256(request['mechanism'].encode()).hexdigest()}
        row['experiment_id']=source_sha(row)
        if any(r['experiment_id']==row['experiment_id'] for r in self.requests):return False
        self.requests.append(row);return True
    def _native_plan_due(self):
        if self.trigger=='stall':return (self.since or 0)>=self.stall_evals
        last=self.interventions[-1].get('at') if self.interventions else self.period_first-self.period
        return (self.attempts or 0)-(last or 0)>=self.period
    def _can_probe(self):
        return bool(self.requests and self.interventions and any(i.get('children') for i in self.interventions) and not self.probes and not self.pending and not self.open_dirs and not self._planning and not self._native_plan_due() and not self._active((SPROUT,)) and self.slots.remaining()>=2 and self.slots.probe+2<=self.slots.diagnostic and self._financial_headroom())
    def _probe_item(self,ctx):
        if not self._can_probe():return None
        while self.requests:
            row=self.requests.pop(0);context=row['context'];anchor=ctx.store.get(context['anchor_id'])
            owner=row['owner'];intervention=owner.get('intervention');direction_id=owner.get('native_direction_id')
            native_children=self.interventions[intervention].get('children',[]) if isinstance(intervention,int) and 0<=intervention<len(self.interventions) else []
            native_child=next((x.get('child') for x in native_children if x.get('direction')==direction_id and x.get('child') and not x.get('not_produced')),None)
            native_program=ctx.store.get(native_child) if native_child else None
            if native_program is None:
                self.rejected_requests.append({**row,'reason':'native_direction_not_executed'});continue
            row['native_child_id']=native_child;row['native_child_source_sha256']=source_sha(native_program.genome.files)
            chains=[c for c,ch in enumerate(self.chains) if not self._retired(c) and context['anchor_id'] in ch]
            if anchor is None or source_sha(anchor.genome.files)!=context['anchor_source_sha256'] or context['evaluation_fingerprint']!=self.fingerprint or context['objective_sha256']!=hashlib.sha256(ctx.objective.encode()).hexdigest() or chains!=context['chains'] or not chains:
                self.rejected_requests.append({**row,'reason':'stale_anchor_or_context'});continue
            if self.slots.reserve(2,probe=True)!=2:return None
            item=Create(kind=self.kind,parent_ids=[anchor.id],anchor_id=anchor.id,k=2,context=PromptContext(instruction=ctx.objective,parents=[anchor],extra={'discovery_memory':self._memory(ctx,[anchor.id])}),meta={'molt_discovery_probe':True,'molt_discovery_request':row['request'],'discovery_experiment_id':row['experiment_id']})
            self.probes[item.batch_id]={**row,'batch_id':item.batch_id,'context_sha256':source_sha(context),'anchor_score':first_score(anchor,self.score_key),'arms':{},'resolved':0,'unknown_ids':[],'failures':[]}
            return item
        return None
    async def ask(self,ctx,n):
        out=[]
        # A possible residual slot is kept for one probe; calling native ask first
        # may itself plan a cohort, which immediately revokes probe eligibility.
        native_target=max(0,n-1) if self._can_probe() else n
        async def native_one():
            available=self.slots.remaining()
            if not available:return []
            k=self.k
            try:
                self.k=min(k,available)
                items=await Molt.ask(self,ctx,1)
            finally:self.k=k
            for item in items:
                if self.slots.reserve(item.k)!=item.k:raise RuntimeError('Atomic candidate reservation invariant failed')
                item.context.extra['discovery_memory']=self._memory(ctx,item.parent_ids)
            return items
        for _ in range(native_target):
            items=await native_one()
            if not items:break
            out.extend(items)
        if len(out)<n and self._can_probe():
            item=self._probe_item(ctx)
            if item:out.append(item)
        while len(out)<n and self.slots.remaining():
            items=await native_one()
            if not items:break
            out.extend(items)
        return out
    async def on_measured(self,ctx,ind,m):
        p=self.probes.get(m.batch_id)
        if p is None:
            if m.batch_id not in self.closed_probes:self.native_callbacks+=1;await super().on_measured(ctx,ind,m)
            return
        arm=None;path=None;files=ind.genome.files
        for candidate,code in files.items():
            if switch_value(code,candidate) is not None:path=candidate;arm='treatment' if switch_value(code,candidate) else 'control';break
        if arm in p['arms'] or (arm is None and ind.id in p['unknown_ids']):return
        if arm is None:p['unknown_ids'].append(ind.id);p['failures'].append({'reason':'unrecognized arm','id':ind.id})
        else:p['arms'][arm]={'id':ind.id,'score':usable_score(m,self.score_key),'files':dict(files),'path':path,'source_sha256':source_sha(files),'measurement':m.__dict__.copy()}
        p['resolved']+=1
        if p['resolved']>=2:self._finish_probe(ctx,m.batch_id)
    async def on_failed(self,ctx,f):
        p=self.probes.get(f.batch_id)
        if p is None:
            if f.batch_id not in self.closed_probes:await super().on_failed(ctx,f)
            return
        ind=ctx.store.get((f.detail or {}).get('individual_id',''))
        if ind is not None:
            ms=[m for m in ind.measurements if m.batch_id==f.batch_id]
            m=ms[-1] if ms else Measurement(individual_id=ind.id,batch_id=f.batch_id,ok=False,artifacts={'evaluation_error':f.reason})
            await self.on_measured(ctx,ind,m);return
        p['failures'].append({'stage':f.stage,'reason':f.reason,'detail':f.detail});p['resolved']+=1
        if p['resolved']>=2:self._finish_probe(ctx,f.batch_id)
    def _finish_probe(self,ctx,batch):
        p=self.probes.pop(batch,None)
        if p is None:return
        self.closed_probes.add(batch);a=p['arms'].get('control',{});b=p['arms'].get('treatment',{});comparable=False;digest=None
        try:
            off,on,digest=audited_pair(a['files'],a['path']);comparable=off==a['files'] and on==b.get('files')
        except (KeyError,ValueError):pass
        sa,sb=a.get('score'),b.get('score');delta=sb-sa if comparable and sa is not None and sb is not None else None
        reproduced=sa is not None and abs(sa-p['anchor_score'])<=self.control_tolerance*max(abs(p['anchor_score']),1e-12)
        status='inconclusive' if delta is None else ('confounded_control' if not reproduced else 'positive_observed_pair' if delta>0 else 'negative_observed_pair' if delta<0 else 'no_observed_effect')
        row={k:copy.deepcopy(v) for k,v in p.items() if k!='arms'}
        code=b.get('files',{}).get(b.get('path'),'')
        row.update(status=status,delta=delta,anchor_reproduced=reproduced,one_switch_source_contrast=comparable,pair_source_sha256=digest,hypotheses=hypotheses(code,b.get('path') or ''),states={arm:{'individual_id':v['id'],'score':v['score'],'source_sha256':v['source_sha256']} for arm,v in p['arms'].items()},executable_states={arm:v['files'] for arm,v in p['arms'].items()},measurements={arm:v['measurement'] for arm,v in p['arms'].items()},errors=p['failures']+[{'arm':arm,'evaluation_error':v['measurement'].get('artifacts',{}).get('evaluation_error') or v['measurement'].get('artifacts',{}).get('invalid_reason') or v['measurement'].get('metrics',{}).get('invalid_reason')} for arm,v in p['arms'].items() if v['score'] is None],scope='One observed conditional matched-template contrast; no independent repetition or universal causal claim.')
        for arm,state in p['arms'].items():
            measurement=state['measurement'];artifacts=measurement.get('artifacts',{})
            error=artifacts.get('evaluation_error') or artifacts.get('invalid_reason') or artifacts.get('error') or measurement.get('metrics',{}).get('invalid_reason')
            if error:row['errors'].append({'arm':arm,'grader_error':str(error)})
        self.evidence.append(row)
        # Single admission path, no native callback, stall attempt or probation turn.
        eligible=[(v['score'],v['id']) for v in p['arms'].values() if v['score'] is not None]
        if eligible and p['context']['evaluation_fingerprint']==self.fingerprint:
            s,iid=max(eligible)
            if s>=p['anchor_score']:
                for c in p['context']['chains']:
                    if not self._retired(c):self.chains[c].append(iid)
                if self.anchor is None or s>self.anchor+self.progress_rel*abs(self.anchor):self.anchor,self.since=s,0
    def state_dict(self):
        state=super().state_dict();state['discovery']={'schema':1,'config':{k:getattr(self,k) for k in ('max_probe_calls','probe_usd_threshold','native_usd_headroom','run_usd_cap','control_tolerance')},'slots':self.slots.state(),'requests':copy.deepcopy(self.requests),'probes':copy.deepcopy(self.probes),'evidence':copy.deepcopy(self.evidence),'closed':sorted(self.closed_probes),'rejected_requests':copy.deepcopy(self.rejected_requests),'probe_calls':self.probe_calls,'probe_cost_upper_bound':self.probe_cost_upper_bound,'fingerprint':self.fingerprint,'native_callbacks':self.native_callbacks,'planner_request_seq':self.planner_request_seq};return state
    def load_state_dict(self,state):
        super().load_state_dict(state);s=state.get('discovery',{});
        if s.get('schema',1)!=1:raise ValueError('Unsupported discovery checkpoint schema')
        for k,v in s.get('config',{}).items():
            if k in ('max_probe_calls','probe_usd_threshold','native_usd_headroom','run_usd_cap','control_tolerance'):setattr(self,k,v)
        ledger=s.get('slots',{});self.slots=CandidateSlots(ledger.get('total',80),ledger.get('diagnostic',8));self.slots.native=ledger.get('native',0);self.slots.probe=ledger.get('probe',0)
        for k in ('requests','probes','evidence','rejected_requests'):setattr(self,k,copy.deepcopy(s.get(k,[] if k!='probes' else {})))
        self.closed_probes=set(s.get('closed',[]));self.probe_calls=s.get('probe_calls',0);self.probe_cost_upper_bound=s.get('probe_cost_upper_bound',0.);self.fingerprint=s.get('fingerprint',self.fingerprint);self.native_callbacks=s.get('native_callbacks',0);self.planner_request_seq=s.get('planner_request_seq',0)
    def reconcile(self,ctx):
        super().reconcile(ctx)
        for batch,p in list(self.probes.items()):
            row={k:copy.deepcopy(v) for k,v in p.items() if k!='arms'};row.update(status='interrupted_unresolved',delta=None,anchor_reproduced=False,states={a:{'individual_id':v['id'],'score':v['score'],'source_sha256':v['source_sha256']} for a,v in p['arms'].items()},executable_states={a:v['files'] for a,v in p['arms'].items()},hypotheses={},errors=p['failures']);self.evidence.append(row);self.closed_probes.add(batch)
        self.probes.clear()
    def lineage(self,store):
        result=super().lineage(store);result.update(method=self.name,mechanism_evidence=copy.deepcopy(self.evidence),diagnostic_registry=copy.deepcopy(self.probes),candidate_reservations=self.slots.state(),rejected_requests=copy.deepcopy(self.rejected_requests),probe_call_count=self.probe_calls,probe_cost_conservative_upper_bound=self.probe_cost_upper_bound);return result
