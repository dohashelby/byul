"""Closed SGLang wire parser over the immutable native8 hash-chain envelope."""
from __future__ import annotations
import hashlib,json
from guard.adapters import sglang_request_termination_sensor as sensor,sglang_request_termination as metadata
from .owned_capture import strict_json

EVENTS=frozenset({'START','MODULE','END','SG_ASGI','SG_NATIVE','SG_GPU_FENCE','SG_GAP','CHECKPOINT','SEGMENT'})


def canonical(row):return(json.dumps(row,sort_keys=True,separators=(',',':'),ensure_ascii=True)+'\n').encode()


def parse_recording(raw,prepared):
    if type(raw)is not bytes or not 0<len(raw)<=sensor.MAX_BYTES:raise ValueError('SGLANG_RECORDING_BYTES_INVALID')
    lines=raw.splitlines(keepends=True);tail=b''
    if not raw.endswith(b'\n'):tail=lines.pop()
    if not 1<=len(lines)<=sensor.MAX_ROWS:raise ValueError('SGLANG_RECORDING_ROW_BOUND')
    rows=[];previous='0'*64;stamp=-1;ready=set();events=0
    for seq,line in enumerate(lines):
        if len(line)>16384:raise ValueError('SGLANG_RECORDING_LINE_BOUND')
        row=strict_json(line,limit=16384)
        if(type(row)is not dict or set(row)!={'seq','event','monotonic_ns','previous_sha256','payload'}or type(row['seq'])is not int or row['seq']!=seq
            or type(row['monotonic_ns'])is not int or not 0<=row['monotonic_ns']<2**63 or row['monotonic_ns']<stamp or row['previous_sha256']!=previous or canonical(row)!=line
            or type(row['event'])is not str or row['event']not in EVENTS or type(row['payload'])is not dict):raise ValueError('SGLANG_RECORDING_ROW_INVALID')
        previous=hashlib.sha256(line).hexdigest();stamp=row['monotonic_ns'];p=row['payload'];event=row['event']
        if seq==0:
            if(event!='START'or set(p)!={'schema','profile','commit','session','settings_sha256','sensor_sha256','context','producer_authenticated'}
                or p['schema']!=sensor.SCHEMA or p['profile']!=sensor.PROFILE or p['commit']!=sensor.COMMIT or p['session']!=prepared['session']
                or p['settings_sha256']!=prepared['assets']['settings.json']['sha256']or p['sensor_sha256']!=prepared['assets']['guard_lifecycle_sensor.py']['sha256']or p['producer_authenticated']is not False):raise ValueError('SGLANG_RECORDING_PREPARED_BINDING_INVALID')
            c=p['context']
            if(type(c)is not dict or set(c)!={'pid','start_ticks','uid','boot_sha256','pid_namespace','time_namespace','time_offsets_sha256'}or any(type(c[k])is not int or not(0 if k=='uid'else 1)<=c[k]<2**64 for k in ('pid','start_ticks','uid','pid_namespace','time_namespace'))or not all(metadata.hashed(c[k])for k in ('boot_sha256','time_offsets_sha256'))):raise ValueError('SGLANG_RECORDING_CONTEXT_INVALID')
        elif event=='START':raise ValueError('SGLANG_RECORDING_RESTARTED')
        elif event=='MODULE':
            if(set(p)not in ({'module','verified'},{'module','verified','source_sha256','code_sha256'},{'module','verified','source_sha256','code_sha256','inherited'})or p.get('module')not in sensor.SOURCE_SHA or type(p.get('verified'))is not bool or 'inherited'in p and p['inherited']is not True):raise ValueError('SGLANG_RECORDING_MODULE_INVALID')
            if any(k in p and not metadata.hashed(p[k])for k in ('source_sha256','code_sha256')):raise ValueError('SGLANG_RECORDING_MODULE_HASH_INVALID')
            if p['verified']:
                if p.get('source_sha256')!=sensor.SOURCE_SHA[p['module']]or not metadata.hashed(p.get('code_sha256')):raise ValueError('SGLANG_RECORDING_MODULE_HASH_INVALID')
                ready.add(p['module'])
            else:ready.discard(p['module'])
        elif event=='SEGMENT':
            from .lifecycle_rolling import segment_marker
            if not segment_marker(row,prepared['session']):raise ValueError('SGLANG_SEGMENT_INVALID')
        elif event=='CHECKPOINT':
            from .lifecycle_checkpoint import marker
            if not marker(row, prepared['session']):raise ValueError('SGLANG_CHECKPOINT_INVALID')
        elif event=='END':
            if set(p)!={'reason'}or p['reason']not in {'SEALED','PROCESS_EXIT','BOUND_REACHED','CONTROL_UNAVAILABLE','SEGMENT_ROLLOVER'}or seq!=len(lines)-1:raise ValueError('SGLANG_RECORDING_END_INVALID')
        else:
            metadata.validate_payload(event,p);events+=1
            if event=='SG_NATIVE'and p['source_verified']is True and p['source_module']not in ready:raise ValueError('SGLANG_RECORDING_SOURCE_UNAVAILABLE')
            if event in {'SG_NATIVE','SG_GPU_FENCE'}and p.get('qualified_single_scheduler')is True and not {'sglang.srt.runtime_context','sglang.srt.managers.scheduler'}<=ready:raise ValueError('SGLANG_RECORDING_CONFIG_SOURCE_UNAVAILABLE')
        rows.append(row)
    end=rows[-1]['payload']if rows[-1]['event']=='END'else{}
    return rows,{'recording_closed':bool(end)and not tail,'recording_end':end.get('reason'),'incomplete_final_row':bool(tail),'discarded_tail_bytes':len(tail),'discarded_tail_sha256':hashlib.sha256(tail).hexdigest()if tail else None,'source_profile_modules':sorted(ready),'whole_run_coverage_verified':False,'sglang_event_count':events}
