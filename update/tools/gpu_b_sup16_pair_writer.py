"""SUP16 pair metadata; the pinned native17 helper consumes exact LF chain bytes.

Preparation facts are unauthenticated; actual native worker consumption remains
the existing collector's job. This tool starts no process, engine or AWS action.
"""
from pathlib import Path
import argparse,hashlib,importlib.util,json,os,re,stat,sys

ORDERING_HELPER_SHA256='2485ba44ce8398f833477b79a04c86624cc254316b566f399a4bc9b5f085e4bd'

REFERENCE_SHA='077edec6c7a19ddbcbee084d4763a63b72293cee16837cdfcd4b95f7b9f51251'
REFERENCE_BYTES=250500968
PARTIAL_SHA='d72424507d4abf34517c53c61c31946db15f60de13eb6d0ec106c04b9c052af9'
PARTIAL_BYTES=10485760
MODEL_FILES={'config.json','generation_config.json','merges.txt','model.safetensors','special_tokens_map.json','tokenizer_config.json','vocab.json'}
SHA_PATTERN=re.compile('[a-f0-9]{64}')
def digest(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as stream:
  for block in iter(lambda:stream.read(8*1024**2),b''):h.update(block)
 return h.hexdigest()
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
def read_json(path,expected):
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
 with os.fdopen(fd,'rb') as stream:
  before=os.fstat(stream.fileno())
  if not stat.S_ISREG(before.st_mode) or before.st_size>2*1024**2:raise ValueError('SUP16_INPUT_FILE_BOUND')
  raw=stream.read(2*1024**2+1);after=os.fstat(stream.fileno())
 current=Path(path).lstat()
 fields=lambda st:(st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns)
 if fields(before)!=fields(after) or fields(after)!=fields(current) or hashlib.sha256(raw).hexdigest()!=expected:
  raise ValueError('SUP16_INPUT_BYTES_OR_IDENTITY_CHANGED')
 return json.loads(raw)
def run(a):
 own=digest(__file__)
 if own!=a.tool_sha256 or os.geteuid()!=1000:raise ValueError('SUP16_PAIR_WRITER_SOURCE_OR_UID_INVALID')
 binding=read_json(a.binding,a.binding_sha256)
 receipt=read_json(a.staging_receipt,a.staging_receipt_sha256)
 if (set(binding)!={'role','session','environment_ref','target_sha256','source_sha256'}
  or binding['role'] not in {'normal','challenge'} or binding['source_sha256']!=own
  or re.fullmatch('[a-f0-9]{32}',binding['session']) is None
  or not isinstance(binding['environment_ref'],dict) or binding['environment_ref'].get('kind')!='ENV'
  or SHA_PATTERN.fullmatch(binding['target_sha256']) is None):
  raise ValueError('SUP16_PAIR_BINDING_INVALID')
 role=binding['role'];partial=role=='challenge'
 if (receipt.get('schema')!='byul-sup16-staged-artifact/1' or receipt.get('role')!=role
  or receipt.get('producer_sha256')!=a.staging_producer_sha256
  or receipt.get('actual_partial_promoted_as_LAB_fault') is not partial
  or receipt.get('partial_source_sha256')!=(PARTIAL_SHA if partial else None)):
  raise ValueError('SUP16_ACTUAL_ROLE_STAGING_RECEIPT_REQUIRED')
 if digest(a.staging_producer)!=a.staging_producer_sha256:raise ValueError('SUP16_STAGING_PRODUCER_CHANGED')
 root=Path('/models')
 actual=receipt.get('actual_files')
 if not isinstance(actual,list) or len(actual)!=len(MODEL_FILES) or {row.get('path') for row in actual if isinstance(row,dict)}!=MODEL_FILES:raise ValueError('SUP16_EXACT_STAGED_MODEL_FILES_REQUIRED')
 for row in actual:
  if not isinstance(row,dict) or set(row)!={'path','sha256','bytes'} or Path(row['path']).name!=row['path']:
   raise ValueError('SUP16_MODEL_PATH_INVALID')
  path=root/row['path']
  if path.is_symlink() or not path.is_file() or path.stat().st_size!=row['bytes'] or digest(path)!=row['sha256']:
   raise ValueError('SUP16_ACTUAL_STAGED_MODEL_CHANGED')
 manifest=root/'reference-manifest.json'
 if manifest.is_symlink() or digest(manifest)!=receipt['reference_manifest_sha256']:raise ValueError('SUP16_REFERENCE_MANIFEST_CHANGED')
 weights=[row for row in actual if row['path']=='model.safetensors']
 expected_sha,expected_bytes=(PARTIAL_SHA,PARTIAL_BYTES) if partial else (REFERENCE_SHA,REFERENCE_BYTES)
 if len(weights)!=1 or weights[0]['sha256']!=expected_sha or weights[0]['bytes']!=expected_bytes:
  raise ValueError('SUP16_FIXED_ROLE_ARTIFACT_REQUIRED')
 parent=Path('/guard-lifecycle/data');sink=parent/binding['session']
 if parent.is_symlink() or parent.parent.is_symlink() or sink.is_symlink():raise ValueError('SUP16_PRIVATE_SESSION_SYMLINK')
 fd=os.open(sink,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
 try:
  owner=os.fstat(fd)
  if owner.st_uid!=1000 or stat.S_IMODE(owner.st_mode)!=0o700:raise ValueError('SUP16_INSTALLER_PRIVATE_SESSION_REQUIRED')
  ordering=False
  if a.native_recording is not None:
   if digest(a.ordering_helper)!=ORDERING_HELPER_SHA256:raise ValueError('SUP16_ORDERING_HELPER_CHANGED')
   spec=importlib.util.spec_from_file_location('fixed_sup16_native17_ordering',a.ordering_helper)
   helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
   ordering=helper.observed_ordering(a.native_recording,binding)
  packet={'schema':'guard-supply-boot-record/1','profile':'supply-boot-partial-artifact-v1','role':role,
   'environment_ref':binding['environment_ref'],'target_sha256':binding['target_sha256'],'source_sha256':own,
   'change':{'kind':'actual-SDK-incomplete-file-LAB-publication' if partial else 'normal-full-reference-no-change','applied':partial,'denied':False,
    'actor_sha256':own,'sensor_started_before_consumption':ordering},
   'rows':[{'artifact_sha256':expected_sha,'artifact_bytes':expected_bytes,'complete_reference_sha256':REFERENCE_SHA,
    'partial_LAB_publication':partial,'worker_consumption_claimed':False}]}
  # The native installer already owns this directory. Never recreate it or
  # overwrite any native journal, prepared asset or prior boot record.
  output=os.open('boot-record.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o444,dir_fd=fd)
  with os.fdopen(output,'wb') as stream:stream.write(canonical(packet));stream.flush();os.fsync(stream.fileno())
  after=sink.lstat()
  if (after.st_dev,after.st_ino)!=(owner.st_dev,owner.st_ino):raise ValueError('SUP16_PRIVATE_SESSION_REPLACED')
 finally:os.close(fd)
 return dict(record=str(sink/'boot-record.json'),record_sha256=hashlib.sha256(canonical(packet)).hexdigest(),
  producer_authenticated=False,worker_consumption_claimed=False,sensor_started_before_consumption=ordering,
  existing_private_session_used=True,role=role,actual_model_root='/models')
def main():
 p=argparse.ArgumentParser(description=__doc__)
 for name in ['binding','staging-receipt','staging-producer']:p.add_argument('--'+name,type=Path,required=True)
 for name in ['tool-sha256','binding-sha256','staging-receipt-sha256','staging-producer-sha256']:p.add_argument('--'+name,required=True)
 p.add_argument('--native-recording',type=Path)
 p.add_argument('--ordering-helper',type=Path,required=True)
 print(json.dumps(run(p.parse_args()),sort_keys=True))
if __name__=='__main__':main()

# The original staging producer/receipt and both role guards remain unchanged.
