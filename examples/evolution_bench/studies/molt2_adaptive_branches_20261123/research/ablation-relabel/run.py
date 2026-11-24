"""Bounded post-search public mechanism diagnostic; no model or held-out access."""
from pathlib import Path
import concurrent.futures
import hashlib
import json
import os
import shutil
import signal
import subprocess
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def main():
    assert json.loads((ROOT/'research/PUBLIC_REREAD_REPORT.json').read_text())['status'] == 'all_selected_sources_reread'
    plan = json.loads((HERE/'PLAN.json').read_text())
    cfg = json.loads((ROOT/'campaign.json').read_text())
    for name, digest in plan['sources'].items():
        assert hashlib.sha256((HERE/name).read_bytes()).hexdigest() == digest
    worker = '''from pathlib import Path
import os,json
task=Path(os.environ['REREAD_TASK']);config=json.loads((task/'task.json').read_text());os.environ.update(config['env']);os.environ['AHC_TASK_DIR']=str(task);os.environ['TASK_DIR']=str(task)
ns={};exec((task/'evaluator.py').read_text(),ns)
result=ns['evaluate'](os.environ['REREAD_OUTPUT'],fidelity='full')
Path(os.environ['REREAD_OUTPUT'],'RESULT.json').write_text(json.dumps(result,indent=2))
'''

    def slot_run(slot, cores):
        names = list(plan['sources'])
        if slot:
            names.reverse()
        rows = []
        for name in names:
            folder = HERE/'results'/name.removesuffix('.cpp')/f'slot{slot}'
            folder.mkdir(parents=True, exist_ok=False)
            shutil.copy2(HERE/name, folder/'solution.cpp')
            env = dict(PATH=os.environ.get('PATH', '/usr/bin:/bin'), LANG='C.UTF-8',
                       REREAD_TASK=str(ROOT/'tasks/ahc024'), REREAD_OUTPUT=str(folder),
                       OMP_NUM_THREADS='8', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1')
            row = dict(source=name, sha256=plan['sources'][name], slot=slot, cores=cores,
                       manually_modified=name.startswith('manual'), used_for_selection=False)
            start = time.time()
            proc = subprocess.Popen(['taskset', '-c', ','.join(map(str, cores)), cfg['python'], '-c', worker],
                                    env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, start_new_session=True)
            try:
                stdout, stderr = proc.communicate(timeout=650)
                (folder/'stdout.log').write_text(stdout)
                (folder/'stderr.log').write_text(stderr)
                assert proc.returncode == 0, 'Evaluator process failed; see private stderr.log'
                result = json.loads((folder/'RESULT.json').read_text())
                row.update(result=result, status='valid' if result.get('num_accepted') == result.get('num_cases') == 150 else 'invalid')
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.communicate()
                row['status'] = 'timeout'
            except Exception as exc:
                row.update(status='failed', error=str(exc))
            row['wall_seconds'] = time.time()-start
            (folder/'AUDIT.json').write_text(json.dumps(row, indent=2)+'\n')
            rows.append(row)
        return rows

    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(slot_run, i, cores) for i, cores in enumerate(cfg['cpu_slots'])]
        rows = [row for future in futures for row in future.result()]
    report = dict(rows=rows, no_model_calls=True, validation_accessed=False, selection_changed=False,
                  limitation=plan['limitations'], intervention=plan['single_intervention'])
    (HERE/'REPORT.json').write_text(json.dumps(report, indent=2)+'\n')


if __name__ == '__main__':
    main()
