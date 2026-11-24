"""Offline launch checks: sources, exact CLI, public assets, controls and funding."""
from pathlib import Path
import argparse,ast,hashlib,json,os,subprocess,sys,time
R=Path(__file__).resolve().parent
cfg=json.loads((R/'campaign.json').read_text());engine=Path(cfg['engine_root'])
assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=engine,text=True).strip()==cfg['engine_commit']
assert not subprocess.check_output(['git','status','--porcelain'],cwd=engine,text=True).strip()
for name,sha in json.loads((R/'SOURCE_FREEZE.json').read_text())['files'].items():
    assert hashlib.sha256((R/name).read_bytes()).hexdigest()==sha,name
runtime=json.loads((R/'research/RUNTIME_DEPENDENCIES.json').read_text())
for name,sha in runtime.items():assert hashlib.sha256(Path(name).read_bytes()).hexdigest()==sha,name
# Verify preservation against the frozen native control snapshot, not just Git status.
control=Path('/home/erwinpi/molt2-lns-coupled-engine')
for name in ['molt.py','simpletes.py']:
    rel=Path('pantheon/evolution/methods')/name
    assert (engine/rel).read_bytes()==(control/rel).read_bytes(),name
script=engine/cfg['entrypoint'];tree=ast.parse(script.read_text())
block=next(n for n in tree.body if isinstance(n,ast.If) and ast.unparse(n.test)=="__name__ == '__main__'")
definitions=[]
for node in block.body:
    if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='args' for t in node.targets):break
    definitions.append(node)
ns={'argparse':argparse,'os':os}
exec(compile(ast.Module(body=definitions,type_ignores=[]),str(script),'exec'),ns)
jobs=json.loads((R/'manifest.json').read_text());assert len(jobs)==40
for job in jobs:
    args=ns['p'].parse_args(job['argv'])
    assert args.method==job['method'] and args.seed==job['seed'] and args.workers==2
    assert args.max_eval_calls==80 and args.max_llm_usd==.5 and args.max_llm_calls==400
    assert args.max_output_tokens==32768 and args.reasoning_off and args.reply_retries==3
    assert args.llm_providers=='DeepInfra' and args.model=='openrouter/deepseek/deepseek-v4.1-flash'
    assert args.eval_timeout==600 and not args.eval_server and not args.resume
    assert job['phase']=='confirm'
    task=Path(args.task);old=Path('/home/erwinpi/molt2-lns-coupled-lab/tasks')/job['task']
    for name in ['solution.cpp','objective.md','evaluator.py','task.json']:
        assert (task/name).read_bytes()==(old/name).read_bytes(),name
    task_cfg=json.loads((task/'task.json').read_text())
    assert task_cfg['inner_fidelity']=='full' and task_cfg['env']['AHC_EVAL_RUNS']=='1'
    constants={}
    for node in ast.parse((task/'evaluator.py').read_text()).body:
        if isinstance(node,ast.Assign) and len(node.targets)==1 and isinstance(node.targets[0],ast.Name):
            try:constants[node.targets[0].id]=ast.literal_eval(node.value)
            except (ValueError,TypeError):pass
    assert constants['FULL_CASES']==150
assert len({(j['task'],j['method'],j['seed']) for j in jobs})==40
shared=Path(cfg['shared_ledger_root']);ledger=json.loads((shared/'spend-ledger.json').read_text())
assert not (shared/'GLOBAL_STOP.json').exists() and not (R/'GLOBAL_STOP.json').exists()
assert cfg['global_usd_cap']==74 and json.loads((shared/'CURRENT_BUDGET.json').read_text())['new_cap_usd']==74
remaining=74-ledger['spent_usd']-sum(ledger['reserved'].values());assert remaining>=24
affinity=set(os.sched_getaffinity(0));assert set(sum(cfg['cpu_slots'],[]))<=affinity
assert len(set(sum(cfg['cpu_slots'],[])))==sum(map(len,cfg['cpu_slots']))
sys.path.insert(0,str(engine))
import runpy
build=runpy.run_path(str(script),run_name='offline_contract')['build_method']
types={name:type(build(name,400)).__name__ for name in cfg['methods']}
assert types=={'simpletes':'SimpleTES','molt':'Molt','molt_modified':'ModifiedMolt','molt2_adaptive_branches':'Molt2AdaptiveBranches'}
result={'status':'passed','jobs':len(jobs),'native_method_types':types,'runtime_public_assets_verified':len(runtime),'source_files_verified':len(json.loads((R/'SOURCE_FREEZE.json').read_text())['files']),'remaining_math_usd':remaining,'validation_opened':False,'new_model_calls':0,'at_unix':time.time()}
(R/'research/PREFLIGHT.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
