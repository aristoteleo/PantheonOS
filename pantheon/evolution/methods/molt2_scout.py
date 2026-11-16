"""Separate, metered component experimenter; native Molt planning is unchanged.

An unvalidated prototype. Questions and templates share four diagnostic model
calls and the same search-dollar/evaluation budgets. No paid experiment implied.
"""
import asyncio,copy,hashlib,json
from dataclasses import replace
from .molt import Molt,BASE,ESTABLISHED,SPROUT,_json
from .molt2 import source_sha,usable_score
from .molt2_discovery import Molt2Discovery

class Molt2Scout(Molt2Discovery):
    name='molt2_scout'
    def __init__(self,**kwargs):
        super().__init__(**kwargs)
        self.scout_questions=0;self.scouted_interventions=set();self.scout_audit=[]
    async def _llm(self,prompt):
        # In particular: no optional appendix, required fields, or experiment
        # memory in native architecture/dedup planner requests.
        return await Molt._llm(self,prompt)
    async def _plan(self,ctx):
        # Never harvest unsolicited measurement requests from native directions.
        return await Molt._plan(self,ctx)
    def _eligible_anchor(self,ident):
        roles=[self.role.get(c) for c,ch in enumerate(self.chains) if not self._retired(c) and ident in ch]
        return any(r in (BASE,ESTABLISHED) for r in roles) and SPROUT not in roles
    def _full_score(self,ind):
        if ind is None:return None
        for measurement in ind.measurements:
            if measurement.metrics.get('validity')==1:
                score=usable_score(measurement,self.score_key)
                if score is not None:return score
        return None
    def _native_planning_priority(self):
        if not self._native_plan_due():return False
        probation=len(self._active((SPROUT,)))
        blocked=probation>=self.max_sprouts if self.plan_during_probation else probation>0
        return not blocked
    def _science_idle(self):
        return bool(self.interventions and any(i.get('children') for i in self.interventions)
                    and not self.probes and not self.pending and not self.open_dirs and not self._planning
                    and not self._native_planning_priority() and self.slots.remaining()>=2
                    and self.slots.probe+2<=self.slots.diagnostic and self._financial_headroom())
    def _can_probe(self):
        return bool(self.requests and self._science_idle())
    def _owner_child(self,ctx,owner):
        index=owner.get('intervention')
        if type(index) is not int or not 0<=index<len(self.interventions):return None
        child=next((c for c in self.interventions[index].get('children',[])
                    if c.get('direction')==owner.get('native_direction_id') and c.get('child')
                    and not c.get('not_produced')),None)
        ind=ctx.store.get(child['child']) if child else None
        if ind is None or not hasattr(ind.genome,'files'):return None
        if owner.get('native_child_id',ind.id)!=ind.id:return None
        if owner.get('native_child_source_sha256',source_sha(ind.genome.files))!=source_sha(ind.genome.files):return None
        return ind
    def _opportunity(self,ctx):
        if self.requests or self.scout_questions>=2 or self.probe_calls+2>self.max_probe_calls or not self._science_idle():return None
        scored=[(self._full_score(i),i) for i in ctx.store if hasattr(i.genome,'files')]
        scored=[(score,ind) for score,ind in scored if score is not None]
        eligible=[(score,ind) for score,ind in scored if self._eligible_anchor(ind.id)]
        if not eligible:return None
        score,anchor=max(eligible,key=lambda pair:pair[0]);frontier=max(x[0] for x in scored)
        if frontier-score>self.probation_margin*max(abs(frontier),1e-12):return None
        for index in reversed(range(len(self.interventions))):
            if index in self.scouted_interventions:continue
            for child in reversed(self.interventions[index].get('children',[])):
                owner={'intervention':index,'native_direction_id':child.get('direction'),'native_direction_type':child.get('type')}
                ind=self._owner_child(ctx,owner)
                if ind is not None:
                    owner.update(native_child_id=ind.id,native_child_source_sha256=source_sha(ind.genome.files))
                    return anchor,owner
        return None
    def _question_prompt(self,ctx,anchor,owner):
        code=json.dumps(anchor.genome.files,ensure_ascii=False)
        directions=self.interventions[owner['intervention']].get('directions',[])
        direction=next((d for d in directions if isinstance(d,dict) and d.get('id')==owner['native_direction_id']),None)
        direction_projection=json.dumps(direction,ensure_ascii=False)[:6000] if direction else '[No persisted direction text; only the executed ownership record is available.]'
        child=self._owner_child(ctx,owner)
        observed_child_score=self._full_score(child)
        if len(code)>20000:code=code[:20000]+'\n[code projection truncated; full source hash is authoritative]'
        return ('# Component experiment decision\nNative architecture planning has already executed the direction below. Do not propose new architecture directions here.\n'
                'Use public-development feedback only. Never access hidden inputs, scoring code or external data.\n'
                'Decide if one OFF/ON component contrast on the exact established anchor would inform a specific next edit. You may decline.\n'
                'Return JSON {"experiment":null,"reason":"..."} or {"experiment":{"anchor_id":"exact supplied ID","mechanism":"one component","decision_to_inform":"specific choice","predicted_contrast":"expected measured difference","falsifier":"what would count against it","expected_cost":{"candidate_evaluations":2,"template_model_calls":1}}}.\n'
                'A failed implementation is not evidence against an abstract algorithm family. Do not describe unmeasured effects as established.\n'
                'Task:\n'+ctx.objective[:6000]+'\nExecuted native owner:\n'+json.dumps(owner)+'\nExecuted direction context:\n'+direction_projection+'\nChild first fully valid public score: '+str(observed_child_score)+'\nAnchor ID: '+anchor.id+'\nSource SHA256: '+source_sha(anchor.genome.files)+'\nAnchor code:\n'+code+'\nContext-scoped prior evidence:\n'+self._memory(ctx,[anchor.id]))
    async def _experiment_llm(self,prompt):
        from openai import AsyncOpenAI
        from pantheon.utils.llm_providers import detect_provider
        from ..variators.usage import add_response
        import os
        cfg=detect_provider(self.model,False)
        client=AsyncOpenAI(api_key=cfg.api_key or os.environ.get('OPENAI_API_KEY'),base_url=cfg.base_url or os.environ.get('OPENAI_API_BASE') or None,timeout=self.timeout)
        extra={'usage':{'include':True},'reasoning':{'enabled':False}}
        if self.providers:extra['provider']={'order':self.providers,'allow_fallbacks':False}
        response=await client.chat.completions.create(model=cfg.model_name,extra_body=extra,messages=[{'role':'user','content':prompt}],max_tokens=min(self.max_tokens or 4096,4096))
        add_response(response)
        return (response.choices[0].message.content or '') if response.choices else ''
    async def _question(self,ctx,anchor,owner):
        index=owner.get('intervention')
        if self.scout_questions>=2 or index in self.scouted_interventions or self.probe_calls+2>self.max_probe_calls:return False
        if self._owner_child(ctx,owner) is None or not self._eligible_anchor(anchor.id) or self._full_score(anchor) is None:return False
        if not self.reserve_probe_call():return False
        self.scout_questions+=1;self.scouted_interventions.add(index)
        prompt=self._question_prompt(ctx,anchor,owner);anchor_hash=source_sha(anchor.genome.files)
        record={'owner':copy.deepcopy(owner),'anchor_id':anchor.id,'anchor_source_sha256':anchor_hash,
                'evaluation_fingerprint':self.fingerprint,'objective_sha256':hashlib.sha256(ctx.objective.encode()).hexdigest(),
                'prompt':prompt,'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest(),'status':'pending','raw_reply':None}
        self.scout_audit.append(record);before=self._usage_cost()
        try:
            reply=await self._experiment_llm(prompt)
        except asyncio.CancelledError:
            record['status']='cancelled';raise
        except Exception as e:
            record.update(status='model_error',error_type=type(e).__name__);return False
        finally:self.probe_cost_upper_bound+=max(0.,self._usage_cost()-before)
        record['raw_reply']=reply;parsed=_json(reply)
        if not isinstance(parsed,dict) or 'experiment' not in parsed:
            record['status']='malformed';return False
        request=parsed['experiment']
        if request is None:record['status']='declined';record['reason']=parsed.get('reason');return False
        fields=('anchor_id','mechanism','decision_to_inform','predicted_contrast','falsifier')
        valid=isinstance(request,dict) and all(isinstance(request.get(k),str) and request[k].strip() for k in fields)
        cost=request.get('expected_cost') if isinstance(request,dict) else None
        if not valid or request['anchor_id']!=anchor.id or not isinstance(cost,dict) or cost!={'candidate_evaluations':2,'template_model_calls':1} or any(type(v) is not int for v in cost.values()):
            record['status']='malformed';return False
        if source_sha(anchor.genome.files)!=anchor_hash or self.fingerprint!=record['evaluation_fingerprint'] or hashlib.sha256(ctx.objective.encode()).hexdigest()!=record['objective_sha256'] or self._owner_child(ctx,owner) is None:
            record['status']='stale_context';return False
        accepted=self.queue_evidence_request(ctx,request,owner);record['status']='queued' if accepted else 'rejected_context'
        return accepted
    async def ask(self,ctx,n):
        if n<=0:return []
        self._sync_stall(ctx);self._review_sprouts(ctx)
        opportunity=self._opportunity(ctx)
        if opportunity:await self._question(ctx,*opportunity)
        return await Molt2Discovery.ask(self,ctx,n)
    def queue_evidence_request(self,ctx,request,owner):
        if not self._eligible_anchor(request.get('anchor_id')) or self._owner_child(ctx,owner) is None:return False
        anchor=ctx.store.get(request.get('anchor_id'))
        if self._full_score(anchor) is None:return False
        return super().queue_evidence_request(ctx,request,owner)
    def _probe_item(self,ctx):
        kept=[]
        scores=[self._full_score(ind) for ind in ctx.store]
        frontier=max((score for score in scores if score is not None),default=None)
        for request in self.requests:
            ident=request['context']['anchor_id'];score=self._full_score(ctx.store.get(ident))
            relevant=score is not None and frontier is not None and frontier-score<=self.probation_margin*max(abs(frontier),1e-12)
            if self._eligible_anchor(ident) and relevant:kept.append(request)
            else:self.rejected_requests.append({**request,'reason':'anchor_no_longer_established_valid_or_frontier_relevant'})
        self.requests=kept
        return super()._probe_item(ctx)
    async def on_measured(self,ctx,ind,m):
        if m.batch_id in self.probes and m.metrics.get('validity')!=1:
            m=replace(m,ok=False,artifacts={**(m.artifacts or {}),'scout_diagnostic_issue':'Incomplete validity; no component-effect claim'})
        return await super().on_measured(ctx,ind,m)
    def state_dict(self):
        state=super().state_dict();state['scout']={'schema':1,'questions':self.scout_questions,'interventions':sorted(self.scouted_interventions),'audit':copy.deepcopy(self.scout_audit)};return state
    def load_state_dict(self,state):
        super().load_state_dict(state);scout=state.get('scout',{})
        if scout.get('schema',1)!=1:raise ValueError('Unsupported Scout checkpoint schema')
        self.scout_questions=int(scout.get('questions',0))
        if not 0<=self.scout_questions<=2:raise ValueError('Invalid Scout question count')
        self.scouted_interventions=set(scout.get('interventions',[]));self.scout_audit=copy.deepcopy(scout.get('audit',[]))
    def lineage(self,store):
        result=super().lineage(store);result['scout_decisions']=copy.deepcopy(self.scout_audit);return result
