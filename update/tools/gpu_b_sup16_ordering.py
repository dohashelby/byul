"""Current native17 ordering; historical replay uses the retained helper.

Retains the reviewed LF chain and timing predicate. Preparation facts remain
unauthenticated; actual worker consumption belongs to the existing collector.
"""
from pathlib import Path
import hashlib,json,re

# Closed generation table; the historical helper alone accepts e32c source.
PRODUCER_SOURCES = {'vllm-native-lifecycle-metadata/17':
    '5542067206f0d92a68575f6fd9cfbaf1c85fa97c35a707f50d9cb1ee260a0b64'}

def native17_ordering_from_raw(raw,binding):
 # Native17's original emitter and capture parser hash exact LF-terminated
 # ASCII-canonical bytes. This ordering fact is not whole-run coverage.
 if type(raw) is not bytes or not 0<len(raw)<=2*1024**2 or not raw.endswith(b'\n'):
  raise ValueError('SUP16_NATIVE_ORDERING_RECORD_INVALID')
 lines=raw.splitlines(keepends=True);rows=[json.loads(line) for line in lines];previous='0'*64
 for index,(line,row) in enumerate(zip(lines,rows)):
  expected=(json.dumps(row,sort_keys=True,separators=(',',':'),ensure_ascii=True)+'\n').encode()
  if expected!=line or row.get('seq')!=index or row.get('previous_sha256')!=previous:
   raise ValueError('SUP16_NATIVE_ORDERING_CHAIN_INVALID')
  previous=hashlib.sha256(line).hexdigest()
 header=rows[0].get('payload',{})
 if (rows[0].get('event')!='START' or header.get('profile')!='vllm-native-lifecycle-metadata/17'
  or header.get('session')!=binding['session']
  or header.get('sensor_sha256')!=PRODUCER_SOURCES['vllm-native-lifecycle-metadata/17']
  or rows[-1].get('event')!='END'):
  raise ValueError('SUP16_NATIVE_ORDERING_HEADER_INVALID')
 starts=[row['monotonic_ns'] for row in rows if row.get('event')=='NATIVE'
  and row['payload'].get('point')=='DefaultModelLoader.load_weights' and row['payload'].get('phase')=='CALL']
 reads=[row['monotonic_ns'] for row in rows if row.get('event')=='FILE_READ' and row['payload'].get('phase')=='READ_RETURN']
 return bool(starts and reads and rows[0]['monotonic_ns']<min(starts)<=min(reads))

def observed_ordering(path,binding):
 if path is None:return False
 if (path.is_symlink() or path.parent!=Path('/guard-lifecycle/data')/binding['session']
  or not re.fullmatch(r'pid-[1-9][0-9]*-[1-9][0-9]*\.jsonl',path.name)):
  raise ValueError('SUP16_NATIVE_ORDERING_PATH_INVALID')
 with path.open('rb') as stream:raw=stream.read(2*1024**2+1)
 return native17_ordering_from_raw(raw,binding)
