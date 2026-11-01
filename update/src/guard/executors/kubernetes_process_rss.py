"""Direct bounded /proc RSS reads for source-bound session processes only."""
from __future__ import annotations
import json
import re
from guard.models import canonical_json,sha256_hex
from guard.runtime.bounded import strict_json
from .lifecycle_session import safe_owner
from .kubernetes_lifecycle_capture import KubernetesLifecycleReader,KubernetesCaptureError,supervised_source

CONTEXT_FIELDS=frozenset({'pid','start_ticks','uid','boot_sha256','pid_namespace','time_namespace','time_offsets_sha256'})
DIAGNOSTICS=frozenset({'BUDGET_RSS_CONTEXT_CHANGED','BUDGET_RSS_PROCESS_UNAVAILABLE'})
RSS_READER=r'''
import hashlib,json,os,re,sys
from pathlib import Path
expected=json.loads(sys.argv[1]);pid=expected['pid'];process=Path('/proc')/str(pid)
def read(path,limit):
 with path.open('rb') as stream:value=stream.read(limit+1)
 if len(value)>limit:raise ValueError()
 return value
def context():
 stat=read(process/'stat',8192).decode('utf-8');fields=stat[stat.rfind(')')+2:].split()
 status=read(process/'status',16384).decode('ascii').splitlines()
 return {'pid':pid,'start_ticks':int(fields[19]),'uid':int(next(v for v in status if v.startswith('Uid:')).split()[2]),
  'boot_sha256':hashlib.sha256(read(Path('/proc/sys/kernel/random/boot_id'),128)).hexdigest(),
  'pid_namespace':os.stat(process/'ns/pid').st_ino,'time_namespace':os.stat(process/'ns/time').st_ino,
  'time_offsets_sha256':hashlib.sha256(read(process/'timens_offsets',1024)).hexdigest()}
result={'schema':'guard-direct-process-rss/1','rss_bytes':None,'process_context_verified':False,'diagnostic_codes':[]}
try:
 before=context()
 status=read(process/'status',16384).decode('ascii').splitlines()
 rss=[line for line in status if line.startswith('VmRSS:')]
 if len(rss)!=1:raise ValueError()
 match=re.fullmatch(r'VmRSS:\s+([0-9]+)\s+kB',rss[0])
 if match is None:raise ValueError()
 amount=int(match[1])*1024
 if not 0<=amount<2**63:raise ValueError()
 after=context()
 if before==expected==after:result.update(rss_bytes=amount,process_context_verified=True)
 else:result['diagnostic_codes'].append('BUDGET_RSS_CONTEXT_CHANGED')
except Exception:result['diagnostic_codes'].append('BUDGET_RSS_PROCESS_UNAVAILABLE')
print(json.dumps(result,sort_keys=True,separators=(',',':')))
'''
RSS_SOURCE_SHA256=sha256_hex(RSS_READER.encode())
OBSERVATIONS={'budget_process_rss_samples':'list','budget_process_rss_exceeded':'bool'}


def validate_context(value):
    if type(value) is not dict or set(value)!=CONTEXT_FIELDS:raise ValueError('BUDGET_RSS_CONTEXT_INVALID')
    for key in ('pid','start_ticks','uid','pid_namespace','time_namespace'):
        if type(value[key]) is not int or not (0 if key=='uid' else 1)<=value[key]<2**64:
            raise ValueError('BUDGET_RSS_CONTEXT_INVALID')
    if value['pid']>=10**10:raise ValueError('BUDGET_RSS_CONTEXT_INVALID')
    for key in ('boot_sha256','time_offsets_sha256'):
        if type(value[key]) is not str or re.fullmatch('[0-9a-f]{64}',value[key]) is None:
            raise ValueError('BUDGET_RSS_CONTEXT_INVALID')
    return value


def project_samples(samples,maximum):
    verified=[sample['rss_bytes'] for sample in samples if sample['process_context_verified'] is True
        and sample['owner_binding_verified'] is True and type(sample['rss_bytes']) is int
        and sample['query_source_sha256']==RSS_SOURCE_SHA256 and not sample['diagnostic_codes']]
    return {'budget_process_rss_samples':{'value':samples,'collected':True},
        'budget_process_rss_exceeded':{'value':any(amount>maximum for amount in verified) if verified else None,'collected':bool(verified)}}


class KubernetesProcessRSSReader(KubernetesLifecycleReader):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.programs['process_rss']=RSS_READER;self.wrapped['process_rss']=supervised_source(RSS_READER)

    def _program(self,container,argv,max_bytes):
        if (container==self.binding['engine']['container'] and type(argv) is list and len(argv)==4
                and argv[:3]==['python3','-c',RSS_READER] and type(argv[3]) is str and len(argv[3])<=1024 and max_bytes==4096):
            try:
                value=validate_context(strict_json(argv[3].encode(),limit=1024))
                if canonical_json(value).decode()!=argv[3]:raise ValueError
            except (ValueError,TypeError):raise KubernetesCaptureError() from None
            return 'process_rss'
        return super()._program(container,argv,max_bytes)

    def sample_packet(self,packet,stage):
        if stage not in {'before','normal','slow','slow-close','after'}:raise ValueError('BUDGET_RSS_STAGE_INVALID')
        samples=[];seen=set()
        for recording_index,recording in enumerate((packet or {}).get('recordings',[])):
            capture=recording.get('capture')
            if not (capture and capture.get('process_context_verified') is True
                    and recording.get('session_owner_match') is True and recording.get('listed_file_identity_match') is True):continue
            try:
                if capture['records'][0]['payload'].get('session')!=self.session:continue
                context=validate_context(capture['records'][0]['payload']['context'])
                if recording['pid']!=context['pid'] or recording['start_ticks']!=context['start_ticks']:continue
                encoded=canonical_json(context).decode()
                if encoded in seen:continue
                seen.add(encoded)
            except (ValueError,TypeError,KeyError,IndexError):continue
            row={'stage':stage,'rss_bytes':None,'process_context_verified':False,'owner_binding_verified':False,
                'query_source_sha256':RSS_SOURCE_SHA256,'diagnostic_codes':[],
                'session_sha256':sha256_hex(self.session.encode()),'process_context_sha256':sha256_hex(encoded.encode()),
                'recording_index':recording_index,'recording_capture_sha256':capture['provenance']['capture_sha256'],
                'recording_file_identity_sha256':sha256_hex(canonical_json(capture['provenance']['file_identity'])),
                'selected_owner_sha256':sha256_hex(canonical_json(capture['provenance']['owner'])),'query_owner_sha256':None}
            samples.append(row)
            try:
                result=self.exec_bounded(self.binding['engine']['container'],['python3','-c',RSS_READER,encoded],timeout=5,max_bytes=4096)
                value=strict_json(result.stdout.encode(),limit=4096)
                if (type(value) is not dict or set(value)!={'schema','rss_bytes','process_context_verified','diagnostic_codes'}
                        or value['schema']!='guard-direct-process-rss/1' or type(value['process_context_verified']) is not bool
                        or type(value['diagnostic_codes']) is not list or any(type(v) is not str or v not in DIAGNOSTICS for v in value['diagnostic_codes'])
                        or (value['process_context_verified'] and (type(value['rss_bytes']) is not int or not 0<=value['rss_bytes']<2**63 or value['diagnostic_codes']))
                        or (not value['process_context_verified'] and value['rss_bytes'] is not None)):
                    raise ValueError
                row['owner_binding_verified']=result.owner_binding_verified is True
                if row['owner_binding_verified']:
                    row['query_owner_sha256']=sha256_hex(canonical_json(safe_owner(result.owner_before)))
                    row['owner_binding_verified']=row['query_owner_sha256']==row['selected_owner_sha256']
                if row['owner_binding_verified']:
                    row.update(rss_bytes=value['rss_bytes'],process_context_verified=value['process_context_verified'],diagnostic_codes=value['diagnostic_codes'])
                else:row['diagnostic_codes']=['BUDGET_RSS_OWNER_CHANGED']
            except Exception:row['diagnostic_codes']=['BUDGET_RSS_QUERY_UNAVAILABLE']
        if not samples:
            samples.append({'stage':stage,'rss_bytes':None,'process_context_verified':False,'owner_binding_verified':False,
                'query_source_sha256':RSS_SOURCE_SHA256,'diagnostic_codes':['BUDGET_RSS_BOUND_PROCESS_UNAVAILABLE'],
                'session_sha256':sha256_hex(self.session.encode()),'process_context_sha256':None,'recording_index':None,
                'recording_capture_sha256':None,'recording_file_identity_sha256':None,'selected_owner_sha256':None,'query_owner_sha256':None})
        return samples
