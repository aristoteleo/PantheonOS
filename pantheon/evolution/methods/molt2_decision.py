"""Required planner measurement questions plus base-family probes during probation.

An instrument-compliance revision, not evidence of optimizer superiority. Native
Molt architecture/probation code and prior prototype classes remain unchanged.
"""
import copy,json
from .molt import Molt,SPROUT,BASE,ESTABLISHED,_json
from .molt2 import first_score
from .molt2_discovery import Molt2Discovery
from .decision_prompt import build_decision_planner_prompt

class Molt2Decision(Molt2Discovery):
    name='molt2_decision'
    def __init__(self,**kwargs):
        super().__init__(**kwargs);self.planner_contract_audit=[]
    def _eligible_anchor(self,anchor_id):
        roles=[self.role.get(c) for c,ch in enumerate(self.chains) if not self._retired(c) and anchor_id in ch]
        return any(r in (BASE,ESTABLISHED) for r in roles) and SPROUT not in roles
    def _eligible_anchor_ids(self,ctx):
        return [i.id for i in ctx.store if self._eligible_anchor(i.id) and first_score(i,self.score_key) is not None]
    async def _llm(self,prompt):
        planning=self._planning_ctx is not None and prompt.startswith('# Dynamic branch intervention portfolio')
        eligible=[]
        if planning:
            self.planner_request_seq+=1;eligible=self._eligible_anchor_ids(self._planning_ctx)
            prompt=build_decision_planner_prompt(prompt,eligible,self._memory(self._planning_ctx,None))
        # Bypass only the predecessor's optional appendix. Native provider/model
        # accounting/SDK path is retained, including caller's shared40USD guard.
        reply=await Molt._llm(self,prompt)
        if not planning:return reply
        parsed=_json(reply)
        if not isinstance(parsed,dict):
            self.planner_contract_audit.append({'status':'malformed_non_object','eligible_anchors':eligible})
            return json.dumps({'directions':[],'diagnosis':'Malformed non-object planner reply; no directions inferred'})
        directions=parsed.get('directions',[])
        if not isinstance(directions,list):
            self.planner_contract_audit.append({'status':'malformed_directions','eligible_anchors':eligible})
            parsed['directions']=[];return json.dumps(parsed)
        actual=[d for d in directions if isinstance(d,dict)]
        missing=[n for n,d in enumerate(actual) if 'evidence_request' not in d]
        questions=[d['evidence_request'] for d in actual if isinstance(d.get('evidence_request'),dict)]
        valid=[q for q in questions if self._valid_request(q) and q.get('anchor_id') in eligible]
        compliant=not missing and len(actual)==len(directions) and all(d.get('evidence_request') is None or isinstance(d.get('evidence_request'),dict) for d in actual) and len(valid)==len(questions) and (bool(valid) or not eligible)
        self.planner_contract_audit.append({'status':'compliant' if compliant else 'contract_not_met','directions':len(actual),'missing_field_indices':missing,'nonnull_requests':len(questions),'valid_eligible_requests':len(valid),'eligible_anchors':eligible})
        # No fabricated question, no retries/compliance-specific paid calls, and no
        # removal of an otherwise valid native architecture direction.
        return reply
    @staticmethod
    def _valid_request(request):
        if not isinstance(request,dict):return False
        if not all(isinstance(request.get(k),str) and request[k].strip() for k in ('anchor_id','mechanism','decision_to_inform','predicted_contrast','falsifier')):return False
        cost=request.get('expected_cost')
        return isinstance(cost,dict) and type(cost.get('candidate_evaluations')) is int and cost['candidate_evaluations']==2 and type(cost.get('template_model_calls')) is int and cost['template_model_calls']==1
    def queue_evidence_request(self,ctx,request,owner):
        if not self._valid_request(request) or not self._eligible_anchor(request['anchor_id']):return False
        return super().queue_evidence_request(ctx,request,owner)
    def _native_planning_priority(self):
        if not self._native_plan_due():return False
        young=len(self._active((SPROUT,)))
        probation_blocks=young>=self.max_sprouts if self.plan_during_probation else young>0
        return not probation_blocks
    def _can_probe(self):
        return bool(self.requests and self.interventions and any(i.get('children') for i in self.interventions) and not self.probes and not self.pending and not self.open_dirs and not self._planning and not self._native_planning_priority() and self.slots.remaining()>=2 and self.slots.probe+2<=self.slots.diagnostic and self._financial_headroom())
    def _probe_item(self,ctx):
        # Revalidate all queued anchors before parent can scan past a stale request.
        kept=[]
        for request in self.requests:
            if self._eligible_anchor(request['context']['anchor_id']):kept.append(request)
            else:self.rejected_requests.append({**request,'reason':'anchor_on_probation_or_not_established'})
        self.requests=kept
        return super()._probe_item(ctx)
    def state_dict(self):
        s=super().state_dict();s['decision']={'schema':1,'planner_contract_audit':copy.deepcopy(self.planner_contract_audit)};return s
    def load_state_dict(self,state):
        super().load_state_dict(state);s=state.get('decision',{})
        if s.get('schema',1)!=1:raise ValueError('Unsupported decision checkpoint schema')
        self.planner_contract_audit=copy.deepcopy(s.get('planner_contract_audit',[]))
    def lineage(self,store):
        s=super().lineage(store);s['planner_contract_audit']=copy.deepcopy(self.planner_contract_audit);return s
