"""Generic warm-start LNS/VNS with RPUCG programs and recent-window UCB operators.

No task identifiers or evaluator access. Model-inferred line regions define executable
edit neighborhoods. The writer sees complete source; only approved regions are assembled
from its replacement strings. Untouched bytes are inherited, not regenerated. This is
syntactic preservation, not a claim of semantic isolation. Ordinary search remains active.
"""
from __future__ import annotations
import asyncio,copy,hashlib,json,math
from collections import deque
from .molt import Molt,_json,BASE
from .simpletes import SimpleTES,UpstreamCompletionVariator,_Batch,score_of
from ..core.genome import CodeGenome
from ..core.work import Create,Produced,PromptContext


def source_hash(files):
    return hashlib.sha256(json.dumps(files,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def validate_regions(files,regions,scope):
    if scope not in (1,2,3) or not isinstance(regions,list) or not regions:
        raise ValueError('Missing edit regions or invalid scope')
    if len(regions)>(1 if scope==1 else 3 if scope==2 else 12):
        raise ValueError('Too many regions for this scope')
    output=[]
    for region in regions:
        if not isinstance(region,dict) or set(region)!={'path','start_line','end_line'}:
            raise ValueError('Region needs exactly path/start_line/end_line')
        path=region['path'];lo,hi=region['start_line'],region['end_line']
        if path not in files or type(lo) is not int or type(hi) is not int:
            raise ValueError('Unknown file or noninteger line bounds')
        lines=files[path].splitlines(keepends=True)
        if not 1<=lo<=hi<=len(lines):raise ValueError('Region outside source')
        selected=''.join(lines[lo-1:hi])
        if 'EVOLVE-BLOCK-' in selected:raise ValueError('Markers cannot be modified')
        output.append({'path':path,'start_line':lo,'end_line':hi})
    output.sort(key=lambda x:(x['path'],x['start_line']))
    for a,b in zip(output,output[1:]):
        if a['path']==b['path'] and a['end_line']>=b['start_line']:
            raise ValueError('Overlapping regions')
    return output


def apply_replacements(files,regions,replacements,scope):
    regions=validate_regions(files,regions,scope)
    if not isinstance(replacements,list) or len(replacements)!=len(regions):
        raise ValueError('Exactly one replacement per approved region')
    changes={}
    for row in replacements:
        if not isinstance(row,dict) or set(row)!={'region','after'}:
            raise ValueError('Replacement needs region/after only')
        index=row['region'];after=row['after']
        if type(index) is not int or not 0<=index<len(regions) or index in changes or not isinstance(after,str):
            raise ValueError('Invalid or duplicate replacement index')
        if 'EVOLVE-BLOCK-' in after:raise ValueError('Replacement contains markers')
        if not after.strip() or len(after.encode())>65536:raise ValueError('Empty/oversized replacement')
        changes[index]=after if after.endswith('\n') else after+'\n'
    result=copy.deepcopy(files);new_regions=[]
    for path in sorted({x['path'] for x in regions}):
        lines=files[path].splitlines(keepends=True);parts=[];cursor=0;new_line=1
        for i,region in enumerate(regions):
            if region['path']!=path:continue
            lo,hi=region['start_line']-1,region['end_line'];prefix=''.join(lines[cursor:lo]);parts.append(prefix);new_line+=len(prefix.splitlines())
            after=changes[i];parts.append(after);count=len(after.splitlines())
            new_regions.append({'path':path,'start_line':new_line,'end_line':new_line+count-1});new_line+=count;cursor=hi
        parts.append(''.join(lines[cursor:]));result[path]=''.join(parts)
    if result==files:raise ValueError('No-op replacement')
    return result,new_regions


class LNSVariator(UpstreamCompletionVariator):
    async def create(self,ctx,item):
        if 'lns_edit' not in item.meta:return await super().create(ctx,item)
        owner=self.lns_owner;edit=item.meta['lns_edit'];anchor=ctx.store.get(edit['anchor'])
        if anchor is None or source_hash(anchor.genome.files)!=edit['source_hash']:return []
        regions=validate_regions(anchor.genome.files,edit['regions'],edit['scope'])
        numbered={p:'\n'.join(f'{i}: {line}' for i,line in enumerate(code.splitlines(),1)) for p,code in anchor.genome.files.items()}
        references=[{'id':i.id,'score':score_of(i,owner.score_key),'files':i.genome.files} for i in item.context.parents if i.id!=anchor.id]
        prompt=('Implement one meaningful alternative in the approved algorithmic component(s). '
                'All source outside these line regions is inherited byte-for-byte. Read the complete working source and reuse its interfaces/helpers. '
                'Do not recreate unrelated preprocessing or infrastructure. Maintain the proposed design while refining its details. '
                'Return JSON only: {"replacements":[{"region":0,"after":"complete replacement source for this region"}]}. '
                'Use zero-based region indices in the listed order. Include every region exactly once; no new files or marker lines. '
                'You may change multiple statements inside a region; this is not a one-line patch restriction.\n'
                +json.dumps({'objective':ctx.objective,'strategy':edit['strategy'],'scope':edit['scope'],'anchor':anchor.id,
                             'approved_regions':regions,'numbered_full_source':numbered,'other_program_references':references,
                             'recent_failures':item.context.failures},ensure_ascii=False))
        async def one(candidate):
            try:
                reply=await owner._llm(prompt);parsed=_json(reply)
                if not isinstance(parsed,dict):raise ValueError('Expected replacement JSON')
                files,masks=apply_replacements(anchor.genome.files,regions,parsed.get('replacements'),edit['scope'])
                owner.region_registry[edit['arm']+'::'+source_hash(files)]=copy.deepcopy(masks)
                return Produced(genome=CodeGenome(files=files),item_id=item.id,batch_id=item.batch_id,
                                parent_ids=list(item.parent_ids),meta={'lns_arm':edit['arm'],'lns_regions':masks,
                                'lns_scope':edit['scope'],'lns_parent_source_hash':edit['source_hash'],'candidate':candidate})
            except Exception as exc:
                owner.generation_audit.append({'batch':item.batch_id,'candidate':candidate,'arm':edit['arm'],
                                               'status':'rejected_before_evaluation','reason':str(exc)[:1000]})
                return None
        return [p for p in await asyncio.gather(*(one(i) for i in range(item.k))) if p is not None]


class Molt2LNS(Molt):
    """LNS operators inferred from source, VNS scope escalation, two-level selection.

    Generic parameters only. Parent selection stays RPUCG within each trajectory.
    Operator selection uses recent normalized improvement per evaluated candidate.
    One in every two completed batches is reserved for original SimpleTES chains;
    remaining decisions use UCB including ordinary search as an eligible operator.
    New edit branches inherit a strong program then refine their own first offspring,
    including temporary regressions. No weakest-branch score is mislabeled a cause.
    """
    name='molt2_lns';variator_cls=LNSVariator
    def __init__(self,*,operator_window=12,operator_exploration=.35,portfolio_size=4,max_planning_rounds=4,**kwargs):
        if any(type(v) is not int or v<1 for v in [operator_window,portfolio_size,max_planning_rounds]) or not math.isfinite(operator_exploration) or operator_exploration<0:
            raise ValueError('Positive integer windows/portfolio/rounds and finite nonnegative exploration required')
        super().__init__(**kwargs)
        self.operator_window=operator_window;self.operator_exploration=operator_exploration
        self.portfolio_size=portfolio_size;self.max_planning_rounds=max_planning_rounds
        self.operators={'ordinary':{'chain':None,'rewards':[],'draws':0,'scope':0,'active':True}}
        self.region_registry={};self.operator_history=[];self.edit_batches={};self.portfolios=[];self.generation_audit=[];self.decisions=[]
        self.processed_failures=set();self.scope=1;self.last_plan_at=0;self.completed_batches=0;self.seen_measurements=set();self._lns_planning=False
        self.original_chains=self.num_chains;self.last_global=None;self.stall_count=0

    async def start(self,ctx,seeds):
        await super().start(ctx,seeds);self.last_global=self._global_best(ctx)

    def default_variator(self,**kw):
        v=super().default_variator(**kw);v.lns_owner=self;return v

    async def _llm(self,prompt):
        # Writer calls use the same nonreasoning setting as ordinary completion;
        # strategy planning retains native Molt's planner reasoning policy.
        from openai import AsyncOpenAI
        import os
        from pantheon.utils.llm_providers import detect_provider
        from ..variators.usage import add_response
        cfg=detect_provider(self.model,False)
        client=AsyncOpenAI(api_key=cfg.api_key or os.environ.get('OPENAI_API_KEY'),base_url=cfg.base_url or os.environ.get('OPENAI_API_BASE') or None,timeout=self.timeout,max_retries=0)
        is_writer=prompt.startswith('Implement one meaningful alternative')
        extra={'usage':{'include':True},'reasoning':{'enabled':False} if is_writer or not self.planner_reasoning else {'effort':'high'}}
        if self.providers:extra['provider']={'order':self.providers,'allow_fallbacks':False}
        try:
            response=await client.chat.completions.create(model=cfg.model_name,messages=[{'role':'user','content':prompt}],max_tokens=self.max_tokens or 32768,extra_body=extra)
            add_response(response);return response.choices[0].message.content or ''
        except Exception as exc:
            self.generation_audit.append({'status':'model_error','role':'writer' if is_writer else 'planner','error':str(exc)[:1000]});return ''

    def _valid_score(self,ind):
        if ind is None:return None
        for m in reversed(ind.measurements):
            s=m.metrics.get(self.score_key)
            if m.ok and m.metrics.get('validity')==1 and isinstance(s,(int,float)) and math.isfinite(s):return float(s)
        return None

    def _anchor(self,ctx):
        candidates=[i for i in ctx.store.of_kind(self.kind) if self._valid_score(i) is not None]
        return max(candidates,key=self._valid_score) if candidates else None

    def _global_best(self,ctx):
        return self._valid_score(self._anchor(ctx))

    async def _make_portfolio(self,ctx):
        anchor=self._anchor(ctx)
        if anchor is None:return
        files=anchor.genome.files
        numbered={p:'\n'.join(f'{i}: {line}' for i,line in enumerate(code.splitlines(),1)) for p,code in files.items()}
        prompt=('You are defining edit neighborhoods for a GENERAL program-evolution optimizer. '
                'Infer the major algorithmic decisions from THIS program and task; do not use a predefined domain taxonomy. '
                f'Propose up to {self.portfolio_size} meaningfully different decision families, each with one concrete replacement mechanism. '
                'Different numerical settings or alternative implementations of the same hierarchy are not different families. '
                'Show why each family changes a different decision, what remains fixed, and which coupled dependencies need reopening. '
                f'Current VNS scope level={self.scope}: level1 permits one contiguous semantic component; level2 up to3 coupled regions; level3 up to12 regions. '
                'Keep the interface, evaluator/data rules and marker lines unchanged. Regions may span a whole algorithmic function, not just tiny edits. '
                'Use inclusive one-based line intervals from the complete numbered source. At level1 choose one region per family. '
                'Return JSON {"families":[{"name":"short unique name","decision":"decision being reopened",'
                '"hypothesis":"testable benefit","replacement":"specific new mechanism","preserve":"what must stay unchanged",'
                '"regions":[{"path":"existing filename","start_line":1,"end_line":5}]}]}. '
                'No candidate code is needed in this planning call.\n'
                +json.dumps({'objective':ctx.objective,'anchor_score':self._valid_score(anchor),'numbered_source':numbered,
                             'recent_operator_results':{k:v['rewards'][-self.operator_window:] for k,v in self.operators.items()},
                             'previous_families':[p['families'] for p in self.portfolios[-2:]]},ensure_ascii=False))
        reply=await self._llm(prompt);parsed=_json(reply);families=parsed.get('families',[]) if isinstance(parsed,dict) else []
        if not isinstance(families,list):families=[]
        accepted=[];names=set();masks=set()
        for family in families[:self.portfolio_size]:
            try:
                if not isinstance(family,dict) or any(not isinstance(family.get(k),str) or not family[k].strip() for k in ['name','decision','hypothesis','replacement','preserve']):raise ValueError('Missing family explanation')
                regions=validate_regions(files,family.get('regions'),self.scope);name=family['name'].strip();key=json.dumps(regions,sort_keys=True)
                if name.lower() in names or key in masks:raise ValueError('Duplicate name or identical edit region')
                names.add(name.lower());masks.add(key);accepted.append({**family,'regions':regions})
            except ValueError as exc:self.generation_audit.append({'status':'invalid_family','reason':str(exc),'family':family})
        number=len(self.portfolios);record={'round':number,'at_evaluations':len(self.seen_measurements),'anchor':anchor.id,
                 'anchor_source_hash':source_hash(files),'anchor_score':self._valid_score(anchor),'scope':self.scope,
                 'families':copy.deepcopy(accepted),'raw_reply':reply,'semantic_diversity_is_model_proposed_not_proven':True}
        self.portfolios.append(record);self.last_plan_at=len(self.seen_measurements)
        if not accepted:return
        for k,v in self.operators.items():
            if k!='ordinary':v['active']=False
        for index,family in enumerate(accepted):
            c=self._new_chain();self.role[c]=BASE;self.chains[c]=[anchor.id]
            arm=f'round{number}/family{index}'
            self.operators[arm]={'chain':c,'rewards':[],'draws':0,'scope':self.scope,'active':True,
                                'strategy':family,'initial_anchor':anchor.id,'initial_regions':family['regions'],'opening':True}

    def _available(self,ctx):
        busy={b.chain_idx for b in self.open.values()};available=[]
        if any(c not in busy for c in range(self.original_chains)):available.append('ordinary')
        available.extend(k for k,v in self.operators.items() if k!='ordinary' and v['active'] and v['chain'] not in busy)
        return available

    def _choose_operator(self,available):
        if not available:return None
        if self.completed_batches%2==0 and 'ordinary' in available:return 'ordinary'
        window=self.operator_history[-self.operator_window:];total=1+len(window)
        def utility(key):
            op=self.operators[key];recent=[r for arm,r in window if arm==key]
            return float('inf') if not recent else (sum(recent)/len(recent) if recent else 0.)+self.operator_exploration*math.sqrt(math.log(total)/(1+len(recent)))
        return max(available,key=utility)

    async def ask(self,ctx,n):
        if n<=0:return []
        # Plan on measured-program stagnation; retain old operators for busy callbacks.
        trial_done=all(v.get('completed',0)>=2 for key,v in self.operators.items() if key!='ordinary' and v['active'])
        if (trial_done and self.stall_count>=self.stall_evals and len(self.portfolios)<self.max_planning_rounds
            and len(self.seen_measurements)-self.last_plan_at>=self.stall_evals
            and not self._lns_planning):
            self._lns_planning=True
            try:await self._make_portfolio(ctx)
            finally:self._lns_planning=False
            # Failed narrower portfolios motivate a larger neighborhood, not a code reset.
            if self.portfolios:self.scope=min(3,self.scope+1)
        arm=self._choose_operator(self._available(ctx))
        if arm is None:return []
        self.selector.observe(ctx,self.score_key);op=self.operators[arm]
        if arm=='ordinary':
            busy={b.chain_idx for b in self.open.values()};c=next(c for c in range(self.original_chains) if c not in busy)
            # Rotate among original chains even though edit trajectories also exist.
            candidates=[c for c in range(self.original_chains) if c not in busy]
            c=min(candidates,key=lambda c:self.prompt_count.get(c,0));self._next_chain=c
            items=await SimpleTES.ask(self,ctx,1)
            if not items:return []
            item=items[0];assert self.open[item.batch_id].chain_idx<self.original_chains
            anchor_score=max((self._valid_score(ctx.store.get(i)) or 0.) for i in item.parent_ids)
        else:
            c=op['chain'];nodes=self._chain_nodes(ctx,c);picked=self.selector.pick(nodes,self._inspiration_count(len(nodes)),self.rng,self.score_key,c)
            if not picked:return []
            anchor=picked[0];regions=op['initial_regions'] if anchor.id==op['initial_anchor'] else self.region_registry.get(arm+'::'+source_hash(anchor.genome.files),anchor.meta.get('lns_regions') if anchor.meta.get('lns_arm')==arm else None)
            try:regions=validate_regions(anchor.genome.files,regions,op['scope'])
            except ValueError:op['active']=False;return await self.ask(ctx,n)
            edit={'arm':arm,'anchor':anchor.id,'source_hash':source_hash(anchor.genome.files),'regions':regions,'scope':op['scope'],'strategy':op['strategy']}
            item=Create(kind=self.kind,parent_ids=[i.id for i in picked],k=self.k,
                        context=PromptContext(instruction=ctx.objective,parents=picked,history=self._history_text(ctx,c),failures=dict(self.failures[c])),
                        meta={'chain':c,'lns_edit':edit})
            self.open[item.batch_id]=_Batch(chain_idx=c,parent_ids=list(item.parent_ids),k=self.k)
            self.prompt_count[c]=self.prompt_count.get(c,0)+1;anchor_score=self._valid_score(anchor)
        op['draws']+=1
        self.edit_batches[item.batch_id]={'arm':arm,'anchor_score':anchor_score,'frontier_score':self._global_best(ctx),'resolved':0,'scores':[],'measured':0,'k':item.k}
        self.decisions.append({'batch':item.batch_id,'arm':arm,'at_evaluations':len(self.seen_measurements),'scope':op['scope'],'anchor_score':anchor_score})
        return [item]

    async def on_measured(self,ctx,ind,m):
        token=(m.batch_id,m.item_id,ind.id,m.at)
        if token in self.seen_measurements:return
        self.seen_measurements.add(token);entry=self.edit_batches.get(m.batch_id)
        s=self._valid_score(ind)
        if s is not None and (self.last_global is None or s>self.last_global+self.progress_rel*abs(self.last_global)):
            self.last_global=s;self.stall_count=0;self.scope=1
        else:self.stall_count+=1
        if entry:
            entry['resolved']+=1;entry['measured']+=1
            if s is not None:entry['scores'].append(s)
        await SimpleTES.on_measured(self,ctx,ind,m)
        self._close_batch(m.batch_id)

    async def on_failed(self,ctx,f):
        failure_token=(f.batch_id,f.item_id,f.stage,f.at)
        if failure_token in self.processed_failures:return
        self.processed_failures.add(failure_token)
        entry=self.edit_batches.get(f.batch_id)
        if f.stage!='generate':
            token=('failure',f.batch_id,f.item_id,f.at)
            if token in self.seen_measurements:return
            self.seen_measurements.add(token);self.stall_count+=1
        if entry:entry['resolved']+=1
        await SimpleTES.on_failed(self,ctx,f);self._close_batch(f.batch_id)

    def _close_batch(self,batch):
        entry=self.edit_batches.get(batch)
        if entry is None or entry['resolved']<entry['k']:return
        op=self.operators[entry['arm']];base=entry['frontier_score'];best=max(entry['scores']) if entry['scores'] else None
        reward=0. if best is None or base is None else max(0.,min(1.,(best-base)/max(abs(base),1e-9)))/entry['k']
        self.operator_history.append((entry['arm'],reward));self.operator_history=self.operator_history[-self.operator_window:]
        op['completed']=op.get('completed',0)+1
        op['rewards'].append(reward);op['rewards']=op['rewards'][-self.operator_window:]
        if entry['arm']!='ordinary' and op.get('opening'):
            # Explore the new basin, not an always-better inherited seed in this branch.
            chain=self.chains[op['chain']]
            if len(chain)>1:self.chains[op['chain']]=chain[1:];op['opening']=False
            elif not entry['scores']:op['active']=False
        self.completed_batches+=1;self.edit_batches.pop(batch,None)

    def state_dict(self):
        out=super().state_dict();out['lns']={'operators':self.operators,'portfolios':self.portfolios,
            'operator_history':self.operator_history,'region_registry':self.region_registry,'generation_audit':self.generation_audit,'decisions':self.decisions,'scope':self.scope,'last_plan_at':self.last_plan_at,
            'pending_batches':copy.deepcopy(self.edit_batches),'processed_failures':[list(x) for x in self.processed_failures],'completed_batches':self.completed_batches,'seen_measurements':[list(x) for x in self.seen_measurements],
            'last_global':self.last_global,'stall_count':self.stall_count,'original_chains':self.original_chains,
            'parameters':{'window':self.operator_window,'exploration':self.operator_exploration,'portfolio_size':self.portfolio_size,'max_rounds':self.max_planning_rounds}}
        return out

    def load_state_dict(self,state):
        super().load_state_dict(state);x=state.get('state',state).get('lns',{})
        for key in ['operators','portfolios','region_registry','operator_history','generation_audit','decisions','scope','last_plan_at','completed_batches','last_global','stall_count','original_chains']:
            if key in x:setattr(self,key,copy.deepcopy(x[key]))
        self.seen_measurements={tuple(t) for t in x.get('seen_measurements',[])};self.processed_failures={tuple(t) for t in x.get('processed_failures',[])};self.edit_batches={}
        for batch in x.get('pending_batches',{}):self.generation_audit.append({'batch':batch,'status':'interrupted_unresolved_no_reward_assigned'})
        p=x.get('parameters',{});self.operator_window=p.get('window',self.operator_window);self.operator_exploration=p.get('exploration',self.operator_exploration);self.portfolio_size=p.get('portfolio_size',self.portfolio_size);self.max_planning_rounds=p.get('max_rounds',self.max_planning_rounds)
