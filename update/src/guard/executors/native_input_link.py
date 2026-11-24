"""Fresh controlled request IDs joined to a passive, still unregistered capture."""
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import secrets

from guard.collectors import native_input
from guard.runtime.kubernetes_native_input import KubernetesNativeInputHTTP,LABELS
from .content_common import sample_artifact,append_artifact


def _received(value,manifest):
    expected={'schema','session','settings_sha256','reader_context','started_ns','finished_ns',
        'clock_wall_started_ns','clock_wall_finished_ns','files','complete'}
    if(type(value)is not dict or set(value)!=expected or value['schema']!='guard-native-input-owned-read/1'
            or value['session']!=manifest['session']or value['settings_sha256']!=manifest['settings_sha256']
            or not native_input._context(value['reader_context'])or value['complete']is not True
            or any(type(value[k])is not int or not 0<=value[k]<2**63 for k in('started_ns','finished_ns','clock_wall_started_ns','clock_wall_finished_ns'))
            or value['finished_ns']<value['started_ns']
            or abs((value['clock_wall_finished_ns']-value['clock_wall_started_ns'])-(value['finished_ns']-value['started_ns']))>100_000_000
            or type(value['files'])is not list or len(value['files'])>4096):raise ValueError('NATIVE_INPUT_READ_INVALID')
    seen=set();total=0
    for item in value['files']:
        if(type(item)is not dict or set(item)!={'name','raw_hex','sha256','context_live'}
                or type(item['name'])is not str or not re.fullmatch(r'pid-[0-9]+-[0-9]+-[0-9]+\.json',item['name'])
                or item['name']in seen or type(item['raw_hex'])is not str or len(item['raw_hex'])>262144
                or not native_input._context(item['context_live'])):raise ValueError('NATIVE_INPUT_READ_INVALID')
        seen.add(item['name']);raw=bytes.fromhex(item['raw_hex']);total+=len(raw)
        if total>3*1024**2 or sha256(raw).hexdigest()!=item['sha256']:raise ValueError('NATIVE_INPUT_READ_BYTES_CHANGED')
        snapshot=json.loads(raw,object_pairs_hook=native_input.runtime.shared._pairs)['snapshot']
        if snapshot['context']!=item['context_live']:raise ValueError('NATIVE_INPUT_READ_GENERATION_CHANGED')
        context=item['context_live'];own=value['reader_context']
        if any(context[k]!=own[k]for k in('boot_sha256','pid_namespace','time_namespace','time_offsets_sha256')):
            raise ValueError('NATIVE_INPUT_READ_GENERATION_CHANGED')
    return value


class Capture:
    def __init__(self,ctx,spec):
        if(type(spec)is not dict or set(spec)-{'prepared_dir','prepared_sha256','prepared_uid'}or not {'prepared_dir','prepared_sha256'}<=set(spec)
                or ctx.check.id not in{'REQ-08','REQ-09'}or ctx.target.get('lab')is not True):
            raise ValueError('NATIVE_INPUT_CAPTURE_SETTINGS_INVALID')
        self.ctx=ctx;self.prepared=Path(spec['prepared_dir']).absolute()
        self.prepared_uid=spec.get('prepared_uid',os.geteuid())
        self.manifest,self.settings,self.profile=native_input.load_prepared(self.prepared,expected_uid=self.prepared_uid)
        if(sha256(native_input.runtime.canonical(self.manifest)).hexdigest()!=spec['prepared_sha256']
                or self.manifest['image']!=ctx.env.engine.image_digest):raise ValueError('NATIVE_INPUT_CAPTURE_BINDING_CHANGED')
        self.transport=getattr(ctx.cprobe,'transport',ctx.cprobe)
        if not isinstance(self.transport,KubernetesNativeInputHTTP):raise ValueError('NATIVE_INPUT_OWNED_TRANSPORT_REQUIRED')
        self.binding=deepcopy(self.transport.binding)
        self.before=_received(self.transport.native_input_records(self.manifest),self.manifest)
        if self.before['reader_context']['uid']!=self.prepared_uid:raise ValueError('NATIVE_INPUT_TARGET_UID_MISMATCH')
        self.requests={};self.cases=[];self.nonce=secrets.token_hex(16)

    def call(self,label,request,dispatch):
        if label not in LABELS or label in self.requests:raise ValueError('NATIVE_INPUT_CASE_INVALID')
        if request.get('headers',{})!={}:raise ValueError('NATIVE_INPUT_REQUEST_HEADERS_INVALID')
        identifier='byul-input-'+self.nonce+'-'+label
        self.transport.declare_request_id(identifier)
        self.requests[label]=identifier
        transmitted={**deepcopy(request),'headers':{'X-Request-Id':identifier}}
        row={'case':label,'request_fixture_sha256':sha256(native_input.runtime.canonical(request.get('body'))).hexdigest(),
            'dispatched':True,'outcome':None};self.cases.append(row)
        result=dispatch(transmitted)
        if type(result)is dict:
            outcome=result.get('outcome');row['outcome']=outcome if type(outcome)is int else'probe_error'
        return result

    def finish(self):
        after=_received(self.transport.native_input_records(self.manifest),self.manifest)
        if after['reader_context']['uid']!=self.prepared_uid:raise ValueError('NATIVE_INPUT_TARGET_UID_MISMATCH')
        if self.binding!=self.transport.binding:raise ValueError('NATIVE_INPUT_TARGET_GENERATION_CHANGED')
        before=self.before;fields=('boot_sha256','pid_namespace','time_namespace','time_offsets_sha256')
        if(any(before['reader_context'][k]!=after['reader_context'][k]for k in fields)
                or before['finished_ns']>after['started_ns']
                or abs((after['clock_wall_started_ns']-before['clock_wall_finished_ns'])-(after['started_ns']-before['finished_ns']))>100_000_000):
            raise ValueError('NATIVE_INPUT_READ_CLOCK_CHANGED')
        original={row['name']:row['sha256']for row in before['files']}
        current={row['name']:row['sha256']for row in after['files']}
        if any(current.get(name)!=digest for name,digest in original.items()):raise ValueError('NATIVE_INPUT_BASELINE_CHANGED')
        reviewed=native_input.verify_records(self.prepared,[(item['name'],bytes.fromhex(item['raw_hex']))for item in after['files']],requests=self.requests,expected_uid=self.prepared_uid)
        for rows in reviewed['requests'].values():
            if any(row['operation_started_ns']<before['finished_ns']or row['observation_ns']>after['started_ns']for row in rows):
                raise ValueError('NATIVE_INPUT_CURRENT_REQUEST_WINDOW_INVALID')
        output=self.ctx.workdir/'native-input-originals';output.mkdir(mode=0o700,parents=True,exist_ok=False)
        for item in after['files']:
            fd=os.open(output/item['name'],os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'wb')as stream:stream.write(bytes.fromhex(item['raw_hex']))
        return {'schema':'guard-native-input-current-link/1','cases':self.cases,'target_generation_stable':True,
            'source_capture':reviewed,'capture_origin':'EXAMPLE'if self.transport.injected else'LIVE_COMPONENT',
            'runtime_qualified':False,'producer_registered':False,'scope_complete':False}


def begin(ctx,cfg):
    if 'native_input_capture'in cfg:ctx._native_input_capture=Capture(ctx,cfg['native_input_capture'])


def case_call(ctx,label,request,dispatch):
    capture=getattr(ctx,'_native_input_capture',None)
    return capture.call(label,request,dispatch)if capture is not None else dispatch(request)


def finish(ctx,draft):
    capture=getattr(ctx,'_native_input_capture',None)
    if capture is None:return draft
    try:summary=capture.finish()
    except Exception:
        summary={'schema':'guard-native-input-current-link/1','cases':capture.cases,
            'diagnostic':'NATIVE_INPUT_CAPTURE_INCOMPLETE','runtime_qualified':False,'producer_registered':False,'scope_complete':False}
        draft.cleanup_errors.append('NATIVE_INPUT_CAPTURE_INCOMPLETE')
        from .http_common import incomplete_artifact_capture
        draft=incomplete_artifact_capture(draft)
    sample_artifact(ctx,'native_input_capture.json',summary)
    return append_artifact(draft,ctx,'native_input_capture.json','native-input-candidate/1')
