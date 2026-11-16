"""Molt2: evolve experimentally tested mechanisms, not only programs.

A protected SimpleTES backbone shares its budget with matched source-code experiments.
One model-written template is materialized with one intervention switch OFF and ON.
Only observations with valid full scores, an audited one-switch source contrast and
an acceptably reproduced anchor can enter the supported-mechanism memory. The
ledger also retains negative, failed and inconclusive experiments. This is an
observed conditional comparison, not a claim of independent or universal causality.

Vanilla Molt and SimpleTES are unchanged. No evaluator/held-out data access lives
in this method; all measurements are provided by the ordinary evolution driver.
"""
from __future__ import annotations

import copy
import ast
import hashlib
import json
import math
import os
import re
from dataclasses import replace
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..core.genome import CodeGenome
from ..core.individual import Individual
from ..core.method import EvolveContext
from ..core.work import Create, Failure, Measurement, Produced, PromptContext
from .simpletes import SimpleTES, UpstreamCompletionVariator, score_of

_PY_SWITCH = re.compile(r'^(?P<head>[ \t]*MOLT2_TREATMENT\s*=\s*)(?P<value>0|1|False|True)(?P<tail>[ \t]*(?:#.*)?)$', re.M)
_CPP_SWITCH = re.compile(r'^(?P<head>[ \t]*(?:(?:static|inline|const|constexpr)\s+)*(?:bool|int)\s+MOLT2_TREATMENT\s*=\s*)(?P<value>false|true|0|1)(?P<tail>[ \t]*;[^\n]*)$', re.M)


def switch_value(code: str, path: str) -> Optional[int]:
    pattern = _PY_SWITCH if path.endswith('.py') else _CPP_SWITCH
    matches = list(pattern.finditer(code))
    if len(matches) != 1:
        return None
    return int(matches[0]['value'] in ('1', 'True', 'true'))


def toggle_pair(files: Dict[str, str], path: str) -> Tuple[Dict[str, str], Dict[str, str], str]:
    """Audit and materialize exactly one OFF/ON binding; no other byte changes."""
    code = files[path]
    pattern = _PY_SWITCH if path.endswith('.py') else _CPP_SWITCH
    matches = list(pattern.finditer(code))
    if len(matches) != 1:
        raise ValueError('Expected exactly one top-level MOLT2_TREATMENT binding')
    if path.endswith('.py'):
        try:
            tree = ast.parse(code)
            bindings = [n for n in tree.body if isinstance(n, ast.Assign) and
                        len(n.targets) == 1 and isinstance(n.targets[0], ast.Name) and
                        n.targets[0].id == 'MOLT2_TREATMENT']
            writes = [n for n in ast.walk(tree) if isinstance(n, ast.Name) and
                      n.id == 'MOLT2_TREATMENT' and isinstance(n.ctx, (ast.Store, ast.Del))]
            shadows = [n for n in ast.walk(tree) if isinstance(n, ast.arg) and
                       n.arg == 'MOLT2_TREATMENT']
            if len(bindings) != 1 or len(writes) != 1 or shadows:
                raise ValueError('Switch must have one real module binding and never be reassigned')
            if bindings[0].lineno != code.count('\n', 0, matches[0].start()) + 1:
                raise ValueError('Switch text must correspond to its real assignment')
            used = any(isinstance(n, ast.Name) and n.id == 'MOLT2_TREATMENT' and
                       isinstance(n.ctx, ast.Load) for n in ast.walk(tree))
        except SyntaxError as exc:
            raise ValueError('Malformed Python experiment template') from exc
    else:
        if not re.search(r'\b(?:const|constexpr)\b', matches[0]['head']):
            raise ValueError('C++ switch must be const or constexpr')
        # Ignore comments and quoted literals when checking the flag is really used.
        stripped = re.sub(r'//[^\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', ' ', code)
        used = len(re.findall(r'\bMOLT2_TREATMENT\b', stripped)) >= 2
    if not used:
        raise ValueError('Treatment switch is declared but not used')
    m = matches[0]
    # A nested Python binding would not necessarily control the implementation.
    if path.endswith('.py') and m['head'][:1] in (' ', '\t'):
        raise ValueError('Python treatment binding must be at module scope')
    zero, one = ('0', '1') if path.endswith('.py') else ('false', 'true')
    off = code[:m.start('value')] + zero + code[m.end('value'):]
    on = code[:m.start('value')] + one + code[m.end('value'):]
    base = dict(files);treatment = dict(files)
    base[path] = off;treatment[path] = on
    canonical = json.dumps(base, sort_keys=True, ensure_ascii=False)
    return base, treatment, hashlib.sha256(canonical.encode()).hexdigest()


def usable_score(m: Measurement, score_key: str = 'combined_score') -> Optional[float]:
    """A successful wrapper is not enough: reject invalid and unscored timeouts."""
    v = m.metrics.get(score_key)
    valid = m.metrics.get('validity', 0)
    if not m.ok or m.fidelity != 'full' or not isinstance(valid, (int, float)) or not math.isfinite(valid) or valid <= 0:
        return None
    if not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return float(v)


def first_score(ind: Optional[Individual], score_key: str) -> Optional[float]:
    if ind is None:
        return None
    for m in ind.measurements:
        s = usable_score(m, score_key)
        if s is not None:
            return s
    return None


def source_sha(files: Dict[str,str]) -> str:
    return hashlib.sha256(json.dumps(files,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def _hypothesis(code: str) -> str:
    m = re.search(r'MOLT2_HYPOTHESIS\s*:\s*([^\n]+)', code)
    return m.group(1).strip(' */#')[:600] if m else 'Unlabelled switch experiment'


PAIR_INSTRUCTION = '''You are designing ONE controlled source-code experiment.
Return ONE complete template, not two independently rewritten programs.

THE CONTROL: faithfully reproduce the named anchor's currently selected algorithm.
THE TREATMENT: change ONE coherent algorithmic mechanism, with a concrete reason it
might improve this task. It may be a different representation/algorithmic principle,
not just a cosmetic change. Retain proven data handling and unrelated mechanisms.

Instrumentation contract (audited mechanically):
- Python: exactly one module-level `MOLT2_TREATMENT = 0` binding.
- C++: exactly one `static constexpr bool MOLT2_TREATMENT = false;` binding.
- Use that variable in the implementation to switch ONLY the proposed mechanism.
- OFF must implement the anchor, ON the proposed mechanism. Both must work standalone.
- A prior anchor may have an older switch: freeze its current behavior first, then
  introduce the single NEW switch. Never keep multiple switch bindings.
- C++: declare exactly `static constexpr bool MOLT2_TREATMENT = false;` at
  file scope, and use it in the changed mechanism. Do not use a preprocessor macro.
- Python: declare exactly `MOLT2_TREATMENT = 0` at module scope and use it.
  The harness alone changes the declaration to true / 1 for the treatment.
- Add a comment `MOLT2_HYPOTHESIS: <one precise mechanism and predicted effect>`.
- Add `MOLT2_FALSIFIER: <what measured result would count against this hypothesis>`.
- No exception-based silent fallback, evaluator access, data leakage or changed I/O.
- Preserve all task constraints and all EVOLVE-BLOCK boundaries/scaffold.
The harness makes OFF and ON copies by changing only the switch literal. It measures
both, checks the control against the frozen anchor, and remembers the observed delta.
An unrelated control rewrite will be flagged as confounded, not supporting evidence.
Avoid repeating failed mechanisms unless you state what changed in the hypothesis.
'''


class Molt2Variator(UpstreamCompletionVariator):
    """One model template -> an audited paired contrast with two normal evaluations."""

    def _evidence_prompt(self, item: Create) -> str:
        return str(item.context.extra.get('molt2_memory', ''))

    def build_block_prompt(self, ctx, item, path, eb):
        ordinary = super().build_block_prompt(ctx, item, path, eb)
        extra = self._evidence_prompt(item)
        if item.meta.get('molt2_experiment'):
            return (ordinary + '\n\n' + PAIR_INSTRUCTION + '\nFrozen anchor id: ' +
                    item.meta['molt2_anchor_id'] + '\n\n' + extra)
        return ordinary + ('\n\nMEASURED MECHANISM MEMORY (observations, not proven laws):\n' + extra if extra else '')

    def build_prompt(self, ctx, item, path, parent_code):
        ordinary = super().build_prompt(ctx, item, path, parent_code)
        extra = self._evidence_prompt(item)
        return ordinary + ('\n\n' + PAIR_INSTRUCTION if item.meta.get('molt2_experiment') else '') + '\n' + extra

    async def create(self, ctx: EvolveContext, item: Create) -> List[Produced]:
        if not item.meta.get('molt2_experiment'):
            return await super().create(ctx, item)
        # k=1 on the provider wire: only a single source template is sampled. The
        # driver's original k=2 still accounts for both OFF and ON evaluations.
        templates = await super().create(ctx, replace(item, k=1))
        if not templates:
            return []
        template = templates[0]
        path = self._file_to_evolve(template.genome.files)
        try:
            off, on, digest = toggle_pair(template.genome.files, path)
        except ValueError:
            return []  # Driver reports both generation shortfalls and closes the pair.
        out = []
        for arm, files in [('control', off), ('treatment', on)]:
            out.append(Produced(genome=CodeGenome(files=files), item_id=item.id,
                batch_id=item.batch_id, parent_ids=list(item.parent_ids), anchor_id=item.anchor_id,
                meta={**template.meta, 'molt2_arm': arm, 'molt2_pair_hash': digest,
                      'molt2_hypothesis': _hypothesis(files[path]),
                      'molt2_frozen_anchor': item.meta['molt2_anchor_id']}))
        return out


class Molt2(SimpleTES):
    """Protected program search plus a memory of measured matched experiments."""
    name = 'molt2'
    variator_cls = Molt2Variator

    def __init__(self, *, experiment_interval: int = 3, warmup: int = 6,
                 experiment_fraction: float = .35, control_tolerance: float = .02,
                 memory_size: int = 8, **kwargs):
        super().__init__(**kwargs)
        self.experiment_interval = max(1, int(experiment_interval))
        self.warmup = max(2, int(warmup))
        self.experiment_fraction = min(.6, max(.05, float(experiment_fraction)))
        self.control_tolerance = max(0., float(control_tolerance))
        self.memory_size = max(1, int(memory_size))
        self.evidence: List[Dict[str, Any]] = []
        self.pending_experiments: Dict[str, Dict[str, Any]] = {}
        self.base_batches = 0
        self.experiment_batches = 0
        self.completed_measurements = 0
        self.usable_measurements = 0
        self.last_experiment_at = 0
        self._closed_experiments = set()
        self._fingerprint = os.environ.get('MOLT2_EVAL_FINGERPRINT', '')

    async def start(self, ctx, seeds: Sequence[Individual]):
        await super().start(ctx, seeds)
        # Experiments share the driver's hard budget, rather than exhausting per-chain caps.
        self.prompt_budget = {}
        if not self._fingerprint:
            self._fingerprint = hashlib.sha256(ctx.objective.encode()).hexdigest()

    def _memory(self) -> str:
        # Include positives and negatives, not a winner-only narrative.
        recent = self.evidence[-self.memory_size:]
        keys = ['hypothesis', 'status', 'anchor_id', 'control_id', 'treatment_id',
                'anchor_score', 'control_score', 'treatment_score', 'delta',
                'anchor_reproduced', 'errors', 'scope']
        return '\n'.join(json.dumps({k:e.get(k) for k in keys}, ensure_ascii=False)
                         for e in recent)

    def _wants_experiment(self, ctx) -> bool:
        if self.pending_experiments or self.usable_measurements < self.warmup:
            return False
        if self.base_batches - self.last_experiment_at < self.experiment_interval:
            return False
        # A hard exploration allowance: at most this share of dispatched batches.
        total = self.base_batches + self.experiment_batches + 1
        return (self.experiment_batches + 1) / total <= self.experiment_fraction

    def _experiment(self, ctx) -> Optional[Create]:
        candidates = []
        for c in range(self.num_chains):
            nodes = [n for n in self._chain_nodes(ctx, c) if first_score(n, self.score_key) is not None]
            if nodes:
                candidates.append((c, nodes[0]))
        if not candidates:
            return None
        # Alternate protected lines; a weak hypothesis never retires a productive base.
        c, anchor = candidates[self.experiment_batches % len(candidates)]
        anchor_score = first_score(anchor, self.score_key)
        frontier = max(first_score(n, self.score_key) for n in ctx.store
                       if first_score(n, self.score_key) is not None)
        item = Create(kind=self.kind, parent_ids=[anchor.id], anchor_id=anchor.id, k=2,
            context=PromptContext(instruction=ctx.objective, parents=[anchor],
                history=self._history_text(ctx,c), failures=dict(self.failures[c]),
                extra={'molt2_memory': self._memory()}),
            meta={'molt2_experiment': True, 'molt2_anchor_id': anchor.id,
                  'chain': c, 'selector': self.selector.name})
        self.pending_experiments[item.batch_id] = {
            'batch_id':item.batch_id, 'anchor_id':anchor.id,
            'anchor_source_sha256':source_sha(anchor.genome.files), 'anchor_score':anchor_score,
            'frontier_score':frontier, 'chain':c, 'arms':{}, 'failures':[], 'resolved':0,
            'evaluation_fingerprint':self._fingerprint,
            'scope':'observed conditional matched-template OFF/ON contrast on the same protocol; anchor agreement is a diagnostic, not proof of semantic parent preservation or independent replication'}
        self.experiment_batches += 1
        self.last_experiment_at = self.base_batches
        return item

    async def ask(self, ctx: EvolveContext, n: int):
        out = []
        if n and self._wants_experiment(ctx):
            item = self._experiment(ctx)
            if item:
                out.append(item)
        if len(out) < n:
            base = await super().ask(ctx, n-len(out))
            for item in base:
                item.context.extra['molt2_memory'] = self._memory()
                item.meta['molt2_operation'] = 'evidence_conditioned_generation'
            self.base_batches += len(base)
            out.extend(base)
        return out

    async def on_measured(self, ctx: EvolveContext, ind: Individual, m: Measurement):
        pending = self.pending_experiments.get(m.batch_id)
        if pending is None:
            if m.batch_id in self._closed_experiments:
                return
            self.completed_measurements += 1
            self.usable_measurements += int(usable_score(m,self.score_key) is not None)
            # Make real grader errors available to future generations, including ones
            # carried in artifacts rather than metrics by CodeEvaluator.
            err = m.artifacts.get('invalid_reason') or m.artifacts.get('evaluation_error') or m.metrics.get('invalid_reason')
            batch = self.open.get(m.batch_id)
            if err and batch is not None:
                book = self.failures[batch.chain_idx]
                text = str(err)[:600];book[text] = book.get(text,0.)+1.
            await super().on_measured(ctx, ind, m)
            return
        # Batch-owned state survives store dedup and arbitrary completion order.
        files = ind.genome.files if isinstance(ind.genome,CodeGenome) else {}
        path = next((p for p,code in files.items() if switch_value(code,p) is not None),None)
        arm = ('treatment' if switch_value(files[path],path) else 'control') if path else 'unknown'
        if arm in pending['arms']:
            return  # Duplicate delivery; never resolve/credit the same arm twice.
        score = usable_score(m,self.score_key)
        pending['arms'][arm] = {'id':ind.id,'score':score,'source_sha256':source_sha(ind.genome.files),
            'files':dict(files),'path':path,'duration':m.duration,
            'errors':str(m.artifacts.get('invalid_reason') or m.artifacts.get('evaluation_error') or
                         m.metrics.get('invalid_reason') or '')[:2000],
            'metrics':dict(m.metrics),'fidelity':m.fidelity}
        pending['resolved'] += 1
        self.completed_measurements += 1
        self.usable_measurements += int(score is not None)
        if pending['resolved'] >= 2:
            self._finish_experiment(ctx,m.batch_id)

    async def on_failed(self,ctx:EvolveContext,f:Failure):
        pending = self.pending_experiments.get(f.batch_id)
        if pending is None:
            if f.batch_id not in self._closed_experiments:
                await super().on_failed(ctx,f)
            return
        pending['failures'].append({'stage':f.stage,'reason':f.reason,'detail':f.detail})
        pending['resolved'] += 1
        if pending['resolved'] >= 2:
            self._finish_experiment(ctx,f.batch_id)

    def _finish_experiment(self,ctx,batch_id):
        p = self.pending_experiments.pop(batch_id,None)
        if p is None:
            return
        self._closed_experiments.add(batch_id)
        arms = p['arms'];a=arms.get('control',{});b=arms.get('treatment',{})
        control,treatment=a.get('score'),b.get('score')
        comparable=False;pairhash=None
        try:
            off,on,pairhash=toggle_pair(a['files'],a['path'])
            comparable=off==a['files'] and on==b.get('files')
        except (KeyError,ValueError,TypeError):
            pass
        reproduced=(control is not None and abs(control-p['anchor_score']) <=
                    self.control_tolerance*max(abs(p['anchor_score']),1e-12))
        delta=treatment-control if comparable and control is not None and treatment is not None else None
        if delta is None:
            status='inconclusive'
        elif not reproduced:
            status='confounded_control'
        elif delta>0:
            status='supported_on_observed_pair'
        elif delta<0:
            status='negative_on_observed_pair'
        else:
            status='no_observed_effect'
        code=b.get('files',{}).get(b.get('path'),'')
        row={k:v for k,v in p.items() if k!='arms'}
        row.update(status=status,hypothesis=_hypothesis(code),control_id=a.get('id'),treatment_id=b.get('id'),
            control_score=control,treatment_score=treatment,delta=delta,
            anchor_reproduced=reproduced,one_switch_source_contrast=comparable,pair_source_sha256=pairhash,
            control_source_sha256=a.get('source_sha256'),treatment_source_sha256=b.get('source_sha256'),
            errors=[v['errors'] for v in arms.values() if v.get('errors')],
            duration=sum(v.get('duration',0.) for v in arms.values()))
        self.evidence.append(row)
        # Measured fitness and scientific support are distinct. A competitive valid
        # child may be admitted even if its mechanistic explanation is inconclusive.
        eligible=[(v['score'],v['id']) for v in arms.values() if v.get('score') is not None]
        if eligible:
            score,iid=max(eligible)
            if score >= p['anchor_score']:
                self.chains[p['chain']].append(iid)
                self.selector.on_commit(p['chain'],[p['anchor_id']],score)

    def state_dict(self):
        state=super().state_dict()
        state['molt2']={'evidence':copy.deepcopy(self.evidence),
            'pending_experiments':copy.deepcopy(self.pending_experiments),
            'base_batches':self.base_batches,'experiment_batches':self.experiment_batches,
            'completed_measurements':self.completed_measurements,'usable_measurements':self.usable_measurements,'last_experiment_at':self.last_experiment_at,
            'closed_experiments':sorted(self._closed_experiments),'evaluation_fingerprint':self._fingerprint}
        return state

    def load_state_dict(self,state):
        super().load_state_dict(state)
        s=state.get('molt2',{})
        self.evidence=copy.deepcopy(s.get('evidence',[]))
        self.pending_experiments=copy.deepcopy(s.get('pending_experiments',{}))
        for k in ['base_batches','experiment_batches','completed_measurements','usable_measurements','last_experiment_at']:
            setattr(self,k,int(s.get(k,0)))
        self._closed_experiments=set(s.get('closed_experiments',[]))
        self._fingerprint=s.get('evaluation_fingerprint',self._fingerprint)

    def reconcile(self,ctx):
        super().reconcile(ctx)
        # The driver does not resume in-flight jobs. Preserve interrupted observations
        # without misclassifying them as negative experiments or resubmitting spend.
        for batch_id,p in list(self.pending_experiments.items()):
            row={k:v for k,v in p.items() if k!='arms'}
            row.update(status='interrupted_unresolved',delta=None,
                       observed_arms={k:{q:v.get(q) for q in ['id','score','source_sha256','errors']}
                                      for k,v in p.get('arms',{}).items()})
            self.evidence.append(row)
            self._closed_experiments.add(batch_id)
        self.pending_experiments.clear()

    def lineage(self,store):
        return {'method':self.name,'mechanism_evidence':copy.deepcopy(self.evidence),
            'pending_experiments':[{k:v for k,v in p.items() if k!='arms'} for p in self.pending_experiments.values()],
            'base_batches':self.base_batches,'experiment_batches':self.experiment_batches,
            'interpretation':'Observed matched contrasts, not independently established causal effects.'}
