"""Copy only prequalified public runtime dependencies; never scan held-out data."""
from pathlib import Path
import json,shutil,hashlib,time,subprocess
R=Path(__file__).resolve().parents[1];expected=json.loads((R/'research/RUNTIME_DEPENDENCIES.json').read_text());dest=R/'research/runtime-assets';dest.mkdir(exist_ok=True);mapping=[]
for name,digest in expected.items():
 p=Path(name);assert p.is_absolute()
 # Only explicitly frozen public files; no wildcard discovery of other datasets.
 assert hashlib.sha256(p.read_bytes()).hexdigest()==digest,name
 rel=Path('files')/p.relative_to('/');q=dest/rel;q.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,q)
 assert hashlib.sha256(q.read_bytes()).hexdigest()==digest
 mapping.append({'original_path':name,'archive_relative':str(q.relative_to(R)),'sha256':digest,'bytes':q.stat().st_size,'resolved_original_path':str(p.resolve())})
inputs=[x for x in mapping if 'public_inputs_150' in x['original_path']];assert len(inputs)==300
for task in ['ahc024','ahc058']:
 names=[Path(x['original_path']).name for x in inputs if f'/{task}_inputs/' in x['original_path']]
 assert sorted(names)==[f'{task}_{i:06d}_input.txt' for i in range(150)]
compiler=subprocess.check_output(['/usr/bin/g++-11','--version'],text=True).splitlines()[0]
manifest={'at_unix':time.time(),'public_input_count':300,'files':mapping,'compiler_version':compiler,'scope':'Exact313 prequalified runtime files including all150 public training cases per task, grader wrappers/testers and compiler binary. No held-out files accessed.','environment_limit':'Saved GCC executable alone is not a portable toolchain. Install matching GCC11 and system libraries; compiler/OS/runtime and CPU timing details affect score reproducibility.'}
(dest/'MANIFEST.json').write_text(json.dumps(manifest,indent=2)+'\n')
print(json.dumps({'preserved_files':len(mapping),'public_inputs':len(inputs),'bytes':sum(x['bytes'] for x in mapping),'compiler':compiler},indent=2))
