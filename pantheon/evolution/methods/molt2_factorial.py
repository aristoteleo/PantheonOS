"""Four executable states, one template, observed conditional factorial evidence.

This is a development prototype, not a claim of established causal identification.
The 00 anchor check is diagnostic; no surrogate scores or heldout access are used.
"""
from __future__ import annotations
import copy,hashlib,json,re,tokenize,io
from dataclasses import replace
from .molt2 import Molt2,source_sha,toggle_pair,usable_score,first_score
from .simpletes import SimpleTES,UpstreamCompletionVariator
from ..core.genome import CodeGenome
from ..core.work import Produced,Measurement

ARMS=('00','10','01','11')
FLAGS=('MOLT2_A','MOLT2_B')

def toggle_factorial(files,path):
    """Validate each real immutable binding, then change only the two literal spans."""
    code=files[path]
    if 'MOLT2_TREATMENT' in code:raise ValueError('Freeze previous experiment flags before a new template')
    # The v1 AST/lexical validator audits actual reads and all Python writes.
    for flag in FLAGS:
        renamed=re.sub(r'\b'+flag+r'\b','MOLT2_TREATMENT',code)
        toggle_pair({path:renamed},path)
    pattern=(r'^(?P<head>MOLT2_[AB]\s*=\s*)(?P<value>0|1|False|True)(?P<tail>[ \t]*(?:#.*)?)$' if path.endswith('.py') else
      r'^(?P<head>[ \t]*static\s+constexpr\s+bool\s+MOLT2_[AB]\s*=\s*)(?P<value>false|true)(?P<tail>[ \t]*;[^\n]*)$')
    matches=list(re.finditer(pattern,code,re.M))
    if len(matches)!=2:raise ValueError('Require exactly two canonical module/file-scope flags')
    spans={next(f for f in FLAGS if f in m['head']):m for m in matches}
    if len(spans)!=2:raise ValueError('Require both A and B')
    if not path.endswith('.py'):
        # Canonical declarations must be real file-scope statements, not a comment,
        # string, nested scope or shadowed local. Compiler checks remaining syntax.
        lexical=re.sub(r'//[^\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'',lambda m:' '*(len(m[0])),code)
        for f,m in spans.items():
            prefix=lexical[:m.start()]
            if prefix.count('{')!=prefix.count('}') or f not in lexical[m.start():m.end()]:raise ValueError('Flags must be real file-scope bindings')
            if len(re.findall(r'\b(?:bool|int|auto)\s+'+f+r'\b',lexical))!=1:raise ValueError('Flag shadowing is forbidden')
    outputs={}
    for arm in ARMS:
        s=code
        for f,m in sorted(spans.items(),key=lambda x:x[1].start(),reverse=True):
            bit=arm[FLAGS.index(f)];value=bit if path.endswith('.py') else ('true' if bit=='1' else 'false')
            s=s[:m.start('value')]+value+s[m.end('value'):]
        outputs[arm]={**files,path:s}
    return outputs,source_sha(outputs['00'])

def factorial_arm(files,path):
    arms,_=toggle_factorial(files,path)
    return next((k for k,v in arms.items() if v==files),None)

def hypotheses(code,path):
    """Read complete multiline comment annotations; never truncate at first line."""
    if path.endswith('.py'):
        try:comments='\n'.join(t.string.lstrip('# ') for t in tokenize.generate_tokens(io.StringIO(code).readline) if t.type==tokenize.COMMENT)
        except tokenize.TokenError:return {}
    else:
        comments='\n'.join(re.sub(r'^\s*(?://|/\*|\*)\s?|\*/\s*$','',m[0],flags=re.M) for m in re.finditer(r'//[^\n]*|/\*[\s\S]*?\*/',code))
    markers=list(re.finditer(r'\bMOLT2_(HYPOTHESIS_A|HYPOTHESIS_B|FALSIFIER|HYPOTHESIS)\s*:\s*',comments))
    return {m[1]:comments[m.end():markers[i+1].start() if i+1<len(markers) else len(comments)].strip() for i,m in enumerate(markers)}

INSTRUCTION='''Design ONE complete 2x2 factorial experiment template, not four rewritten programs.
Use two complementary algorithmic mechanisms: A is a broad architectural axis
(representation/search/decomposition), B is a coherent complementary mechanism
whose usefulness might depend on A. Do not merely split a scalar parameter tweak.
00 must faithfully implement the frozen anchor; 10 adds A alone; 01 adds B alone;
11 combines them. Preserve unrelated behavior, task constraints, scaffold and I/O.
Freeze/remove older experiment switches before introducing these two new switches.
Python: exactly module-level `MOLT2_A = 0` and `MOLT2_B = 0`, no later writes or shadows.
C++: exactly file-scope `static constexpr bool MOLT2_A = false;` and
`static constexpr bool MOLT2_B = false;`, no other declarations of these names.
Both flags must actually control their respective mechanisms, not comments/strings.
The harness alone flips the two literals. All four programs must work standalone.
Comment annotations may span multiple lines. Include MOLT2_HYPOTHESIS_A: and
MOLT2_HYPOTHESIS_B: with mechanisms and predictions, then MOLT2_FALSIFIER:.
No evaluator access, hidden data, exception-based silent fallback, or changed I/O.
Observed main effects are y10-y00 and y01-y00; interaction y11-y10-y01+y00.
These conditional template contrasts are not universal causal laws; anchor agreement
is only diagnostic. Avoid failed mechanisms unless explaining a substantive change.
'''

class Molt2FactorialVariator(UpstreamCompletionVariator):
    def build_block_prompt(self,ctx,item,path,eb):
        ordinary=super().build_block_prompt(ctx,item,path,eb)
        return self._augment(ordinary,item)
    def build_prompt(self,ctx,item,path,parent_code):
        return self._augment(super().build_prompt(ctx,item,path,parent_code),item)
    def _augment(self,prompt,item):
        memory=item.context.extra.get('molt2_memory','')
        if item.meta.get('molt2_experiment'):
            prompt+='\n\n'+INSTRUCTION+'\nFrozen anchor id: '+item.meta['molt2_anchor_id']
        return prompt+'\n\nCODE-LINKED OBSERVATIONS (conditional; negatives retained):\n'+memory if memory else prompt
    async def create(self,ctx,item):
        if not item.meta.get('molt2_experiment'):return await super().create(ctx,item)
        templates=await super().create(ctx,replace(item,k=1))
        if not templates:return []
        template=templates[0];path=self._file_to_evolve(template.genome.files)
        try:arms,digest=toggle_factorial(template.genome.files,path)
        except (KeyError,ValueError):return []
        annotation=hypotheses(template.genome.files[path],path)
        return [Produced(genome=CodeGenome(files=files),item_id=item.id,batch_id=item.batch_id,parent_ids=list(item.parent_ids),anchor_id=item.anchor_id,meta={**template.meta,'molt2_factorial_arm':arm,'molt2_factorial_source_sha256':digest,'molt2_hypotheses':annotation,'molt2_frozen_anchor':item.meta['molt2_anchor_id']}) for arm,files in arms.items()]

class Molt2Factorial(Molt2):
    name='molt2_factorial'
    variator_cls=Molt2FactorialVariator
    def __init__(self,**kwargs):
        super().__init__(**kwargs)
        self.base_eval_slots=0;self.experiment_eval_slots=0;self.seed_eval_slots=0;self.max_eval_calls=None
    async def start(self,ctx,seeds):
        await super().start(ctx,seeds);self.seed_eval_slots=len(seeds)
    def _remaining(self):
        return None if not self.max_eval_calls else max(0,self.max_eval_calls-self.seed_eval_slots-self.base_eval_slots-self.experiment_eval_slots)
    def _wants_experiment(self,ctx):
        remaining=self._remaining()
        if remaining is not None and remaining<4:return False
        if self.pending_experiments or self.usable_measurements<self.warmup or self.base_batches-self.last_experiment_at<self.experiment_interval:return False
        # Reserve four outcomes, not one batch. Generation failures still consume
        # their reserved allowance so a broken template cannot obtain free retries.
        return (self.experiment_eval_slots+4)/(self.base_eval_slots+self.experiment_eval_slots+4)<=self.experiment_fraction
    def _experiment(self,ctx):
        item=super()._experiment(ctx)
        if item is not None:
            item.k=4;item.meta['molt2_factorial']=True
            p=self.pending_experiments[item.batch_id];p['scope']='Observed conditional 2x2 matched-template contrasts under fixed context; anchor agreement is diagnostic, not proof of parent semantics or universal causality.'
            p['expected_arms']=list(ARMS);p['failure_deliveries']=[]
            p['objective_sha256']=hashlib.sha256(ctx.objective.encode()).hexdigest()
            self.experiment_eval_slots+=4
        return item
    async def ask(self,ctx,n):
        out=[]
        if n and self._wants_experiment(ctx):
            item=self._experiment(ctx)
            if item:out.append(item)
        for _ in range(n-len(out)):
            remaining=self._remaining()
            if remaining is not None and remaining<=0:break
            original_k=self.k
            try:
                if remaining is not None:self.k=min(self.k,remaining)
                base=await SimpleTES.ask(self,ctx,1)
            finally:self.k=original_k
            if not base:break
            for item in base:
                item.context.extra['molt2_memory']=self._memory()
                item.meta['molt2_operation']='evidence_conditioned_generation'
                self.base_eval_slots+=item.k;self.base_batches+=1
            out.extend(base)
        return out
    def _memory(self):
        keys=('hypotheses','status','anchor_id','anchor_source_sha256','anchor_score','anchor_reproduced','effects','evaluation_fingerprint','context_sha256','states','errors','scope')
        return '\n'.join(json.dumps({k:e.get(k) for k in keys},ensure_ascii=False) for e in self.evidence[-self.memory_size:])
    async def on_measured(self,ctx,ind,m):
        p=self.pending_experiments.get(m.batch_id)
        if p is None:return await super().on_measured(ctx,ind,m)
        files=ind.genome.files if isinstance(ind.genome,CodeGenome) else {}
        path=None;arm=None
        for candidate in files:
            try:
                found=factorial_arm(files,candidate)
                if found is not None:path=candidate;arm=found;break
            except (ValueError,KeyError,SyntaxError):pass
        # Reject source with no auditable arm as one failed delivery, without
        # assigning zero fitness or fabricating a particular arm.
        if arm is None:
            p['failures'].append({'stage':'audit','reason':'unrecognized factorial source','individual_id':ind.id});p['resolved']+=1
        elif arm in p['arms']:return
        else:
            s=usable_score(m,self.score_key)
            p['arms'][arm]={'id':ind.id,'score':s,'source_sha256':source_sha(files),'files':dict(files),'path':path,'duration':m.duration,'metrics':dict(m.metrics),'fidelity':m.fidelity,'errors':str(m.artifacts.get('invalid_reason') or m.artifacts.get('evaluation_error') or m.artifacts.get('error') or m.metrics.get('invalid_reason') or '')[:2000]}
            p['resolved']+=1;self.completed_measurements+=1;self.usable_measurements+=int(s is not None)
        if p['resolved']>=4:self._finish_experiment(ctx,m.batch_id)
    async def on_failed(self,ctx,f):
        p=self.pending_experiments.get(f.batch_id)
        if p is None:
            if f.batch_id not in self._closed_experiments:await SimpleTES.on_failed(self,ctx,f)
            return
        # Generation shortfalls have identical callback contents and no arm ID;
        # count each driver delivery. Evaluation failures can be deduplicated by ID.
        iid=f.detail.get('individual_id') if f.detail else None
        if iid and iid in p['failure_deliveries']:return
        if iid:p['failure_deliveries'].append(iid)
        iid=(f.detail or {}).get('individual_id')
        ind=ctx.store.get(iid) if iid else None
        if ind is not None:
            measurements=[m for m in ind.measurements if m.batch_id==f.batch_id]
            m=measurements[-1] if measurements else Measurement(individual_id=ind.id,batch_id=f.batch_id,item_id=f.item_id,ok=False,fidelity='full',artifacts={'evaluation_error':f.reason})
            await self.on_measured(ctx,ind,m)
            return
        p['failures'].append({'stage':f.stage,'reason':f.reason,'detail':f.detail});p['resolved']+=1
        if p['resolved']>=4:self._finish_experiment(ctx,f.batch_id)
    def _finish_experiment(self,ctx,batch_id):
        p=self.pending_experiments.pop(batch_id,None)
        if p is None:return
        self._closed_experiments.add(batch_id);arms=p['arms'];comparable=False;digest=None
        try:
            canonical,digest=toggle_factorial(arms['00']['files'],arms['00']['path'])
            comparable=all(arm in arms and canonical[arm]==arms[arm]['files'] for arm in ARMS)
        except (KeyError,ValueError,TypeError):pass
        y={a:arms.get(a,{}).get('score') for a in ARMS}
        reproduced=y['00'] is not None and abs(y['00']-p['anchor_score'])<=self.control_tolerance*max(abs(p['anchor_score']),1e-12)
        effects={'main_a':None,'main_b':None,'interaction':None}
        if comparable:
            if y['00'] is not None and y['10'] is not None:effects['main_a']=y['10']-y['00']
            if y['00'] is not None and y['01'] is not None:effects['main_b']=y['01']-y['00']
            if all(v is not None for v in y.values()):effects['interaction']=y['11']-y['10']-y['01']+y['00']
        complete=comparable and all(v is not None for v in y.values())
        status='inconclusive' if not complete else ('confounded_control' if not reproduced else 'observed_complete_factorial')
        anyarm=next(iter(arms.values()),{});path=anyarm.get('path');annotation=hypotheses(anyarm.get('files',{}).get(path,''),path or '')
        context={'evaluation_fingerprint':p['evaluation_fingerprint'],'anchor_source_sha256':p['anchor_source_sha256'],'factorial_source_sha256':digest,'objective_sha256':p['objective_sha256'],'score_key':self.score_key,'arm_order':list(ARMS)}
        states={a:{'individual_id':v.get('id'),'score':v.get('score'),'source_sha256':v.get('source_sha256'),'path':v.get('path')} for a,v in arms.items()}
        row={k:v for k,v in p.items() if k not in ('arms','failure_deliveries')}
        row.update(status=status,hypotheses=annotation,states=states,scores=y,effects=effects,anchor_reproduced=reproduced,two_switch_source_contrast=comparable,factorial_source_sha256=digest,context_sha256=source_sha(context),errors=[v['errors'] for v in arms.values() if v.get('errors')],duration=sum(v.get('duration',0) for v in arms.values()),executable_states={a:copy.deepcopy(v['files']) for a,v in arms.items()})
        self.evidence.append(row)
        eligible=[(v['score'],v['id']) for v in arms.values() if v.get('score') is not None]
        if eligible:
            s,iid=max(eligible)
            if s>=p['anchor_score']:
                self.chains[p['chain']].append(iid);self.selector.on_commit(p['chain'],[p['anchor_id']],s)
    def state_dict(self):
        s=super().state_dict();s['factorial']={'base_eval_slots':self.base_eval_slots,'experiment_eval_slots':self.experiment_eval_slots,'seed_eval_slots':self.seed_eval_slots};return s
    def load_state_dict(self,state):
        super().load_state_dict(state);s=state.get('factorial',{});self.base_eval_slots=int(s.get('base_eval_slots',0));self.experiment_eval_slots=int(s.get('experiment_eval_slots',0));self.seed_eval_slots=int(s.get('seed_eval_slots',0))
    def reconcile(self,ctx):
        # Preserve executable partial states before v1 closes orphaned jobs.
        for p in self.pending_experiments.values():
            p['executable_states']={a:copy.deepcopy(v['files']) for a,v in p.get('arms',{}).items()}
        pending=copy.deepcopy(self.pending_experiments)
        super().reconcile(ctx)
        for row in self.evidence:
            if row.get("status")=="interrupted_unresolved" and row.get("batch_id") in pending:
                row["executable_states"]={a:v["files"] for a,v in pending[row["batch_id"]].get("arms",{}).items()}
    def lineage(self,store):
        s=super().lineage(store);s.update(base_eval_slots=self.base_eval_slots,experiment_eval_slots=self.experiment_eval_slots);return s
