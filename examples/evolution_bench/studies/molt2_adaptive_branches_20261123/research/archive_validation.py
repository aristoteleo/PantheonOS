"""Validate every declared frozen source and public runtime file inside a tar."""
from pathlib import Path
import json,tarfile,hashlib

def validate_archive(archive,root):
 root=Path(root);freeze=json.loads((root/'SOURCE_FREEZE.json').read_text());runtime=json.loads((root/'research/runtime-assets/MANIFEST.json').read_text())
 expected=dict(freeze['files']);expected.update({x['archive_relative']:x['sha256'] for x in runtime['files']});observed={};packed_freeze=None;packed_runtime=None
 # One sequential pass avoids repeated decompression of large completed-run archives.
 with tarfile.open(archive,'r|gz') as t:
  for member in t:
   if not member.isfile():continue
   if member.name=='SOURCE_FREEZE.json':packed_freeze=json.load(t.extractfile(member))
   elif member.name=='research/runtime-assets/MANIFEST.json':packed_runtime=json.load(t.extractfile(member))
   if member.name in expected:
    digest=hashlib.sha256();stream=t.extractfile(member)
    while block:=stream.read(1024*1024):digest.update(block)
    value=digest.hexdigest();assert value==expected[member.name],'Archive hash mismatch: '+member.name
    if member.name in observed:assert observed[member.name]==value,'Conflicting duplicate archive member'
    observed[member.name]=value
 assert packed_freeze==freeze,'Missing or changed archived source freeze'
 assert packed_runtime==runtime,'Missing or changed archived runtime manifest'
 missing=sorted(set(expected)-set(observed));assert not missing,'Missing archive files: '+str(missing[:10])
 return {'frozen_source_files_verified':len(freeze['files']),'runtime_asset_files_verified':len(runtime['files']),'public_inputs_verified':runtime['public_input_count'],'archive_manifests_consistent':True}
