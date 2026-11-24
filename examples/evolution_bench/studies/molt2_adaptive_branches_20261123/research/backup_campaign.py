"""Immutable launch artifact backup; excludes live run outputs and credentials."""
from pathlib import Path
import tarfile,hashlib,json,time
import modal
R=Path(__file__).resolve().parents[1]
archive=R/'campaign-source.tar.gz'
with tarfile.open(archive,'w:gz') as t:
 for name in ['source','runner','tasks','research','README.txt','campaign.json','manifest.json','SOURCE_FREEZE.json','AUTHORIZATION.json','LAUNCH.json','launch.py','preflight.py','qualify.py','finalize.py']:
  p=R/name
  if p.exists():t.add(p,arcname=name,filter=lambda i:None if '__pycache__' in Path(i.name).parts else i)
from archive_validation import validate_archive
validation=validate_archive(archive,R)
checksum=hashlib.sha256(archive.read_bytes()).hexdigest();remote=f'/archives/molt2-adaptive-branches-20261123/source-{checksum}.tar.gz'
receipt={'sha256':checksum,'bytes':archive.stat().st_size,'at_unix':time.time(),'scope':'Campaign code/configuration/qualification plus preserved public runtime assets; ongoing search traces are archived separately.','local':str(archive),'remote':remote,'volume':'molt-cardiac4d-runs','readback_verified':False,'artifact_validation':validation}
try:
 v=modal.Volume.from_name(receipt['volume'],create_if_missing=False)
 with v.batch_upload(force=True) as b:b.put_file(archive,remote)
 observed=hashlib.sha256()
 for block in v.read_file(remote):observed.update(block)
 assert observed.hexdigest()==checksum
 receipt['readback_verified']=True
except Exception as e:receipt['error']=repr(e)
(R/'research/SOURCE_BACKUP_RECEIPT.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt,indent=2))
