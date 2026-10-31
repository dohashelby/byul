"""Closed endpoint claims and conservative same-held-FIFO candidates for LIF09."""
import re
from . import vllm_ready_endpoint_sensor as sensor,vllm_ready_endpoint_contract as contract
from . import vllm_worker_generation_sensor as generation,vllm_worker_generation_contract as generation_contract
from . import vllm_worker_generation_metadata as previous,vllm_worker_startup_metadata as startup
from . import vllm_initialization_sensor as initialization

BINDING=frozenset({'session_sha256','process_sha256','observer_runtime','observer_code_sha256'})
TRUTH=frozenset(sensor.TRUTH)
FIELDS=frozenset({'schema','point','operation','phase','call_sha256','reader_token','parent_init_seq','parent_init_payload_sha256',
 'wait_seq','wait_payload_sha256','generation_seq','generation_payload_sha256','main_call_sha256','child_pid_claim','rank','start_method',
 'endpoint_before_sha256','endpoint_after_sha256','status','source_sha256','source_code_sha256','delegate_code_sha256',
 'operation_started_ns','operation_finished_ns','exception_kind'})|BINDING|TRUTH
GAP_FIELDS=frozenset({'schema','reason','point'})|BINDING|TRUTH
CLOSE_FIELDS=frozenset({'schema','cleared_readers','selected_observer_closed'})|BINDING|TRUTH
POINTS=frozenset({'WorkerProc.make_worker_process','WorkerProc.wait_for_ready','WorkerProc.worker_main'})
OP_POINTS={'CREATE':'WorkerProc.make_worker_process','RECV':'WorkerProc.wait_for_ready','SEND':'WorkerProc.worker_main'}


def fingerprint(value):return type(value) is str and re.fullmatch('[0-9a-f]{64}',value) is not None
def uint(value):return type(value) is int and 0<=value<2**63
def optional_hash(value):return value is None or fingerprint(value)


def binding(value,fields,schema):
    if type(value) is not dict or set(value)!=fields or value['schema']!=schema:raise ValueError
    if any(not fingerprint(value[k]) for k in ('session_sha256','process_sha256')):raise ValueError
    if (value['observer_runtime'] not in sensor.RUNTIME_CODE_CONTRACTS
            or value['observer_code_sha256']!=sensor.RUNTIME_CODE_CONTRACTS[value['observer_runtime']]
            or any(value[k] is not False for k in TRUTH)):raise ValueError


def validate_event(value):
    try:
        binding(value,FIELDS,sensor.EVENT_SCHEMA);runtime=value['observer_runtime'];op=value['operation']
        if op not in OP_POINTS or value['point']!=OP_POINTS[op] or value['phase'] not in {'CALL','RETURN','RAISE'}:raise ValueError
        for key in ('call_sha256','source_code_sha256'):
            if not fingerprint(value[key]):raise ValueError
        for key in ('reader_token','parent_init_payload_sha256','wait_payload_sha256','generation_payload_sha256',
                    'main_call_sha256','endpoint_before_sha256','endpoint_after_sha256','delegate_code_sha256'):
            if not optional_hash(value[key]):raise ValueError
        for key in ('parent_init_seq','wait_seq','generation_seq','child_pid_claim'):
            if value[key] is not None and not uint(value[key]):raise ValueError
        if value['child_pid_claim']==0:raise ValueError
        if value['rank'] is not None and (type(value['rank']) is not int or not 0<=value['rank']<=63):raise ValueError
        if value['status'] not in {None,'READY','other'} or value['start_method'] not in {None,'spawn','fork'}:raise ValueError
        if (not uint(value['operation_started_ns']) or not uint(value['operation_finished_ns'])
                or value['operation_started_ns']>value['operation_finished_ns']):raise ValueError
        if value['exception_kind'] not in {None,'exception','base-exception','timeout'}:raise ValueError
        if (value['phase']=='RAISE')!=(value['exception_kind'] is not None):raise ValueError
        if value['source_sha256']!=contract.SOURCE_SHA256[contract.MP]:raise ValueError
        expected=(generation_contract.POINT_CODES[runtime] if op=='SEND' else contract.POINT_CODES[runtime])[value['point']]
        if value['source_code_sha256']!=expected:raise ValueError
        if op=='SEND':
            if any(value[k] is not None for k in ('reader_token','parent_init_seq','parent_init_payload_sha256','wait_seq',
                                                  'wait_payload_sha256','child_pid_claim','start_method')):raise ValueError
            if (value['generation_seq'] is None or value['generation_payload_sha256'] is None or value['main_call_sha256'] is None
                    or value['delegate_code_sha256']!=generation_contract.CONNECTION_CODES[runtime]['send']):raise ValueError
        else:
            if value['parent_init_seq'] is None or value['parent_init_payload_sha256'] is None:raise ValueError
            if any(value[k] is not None for k in ('generation_seq','generation_payload_sha256','main_call_sha256')):raise ValueError
            if op=='CREATE':
                if any(value[k] is not None for k in ('wait_seq','wait_payload_sha256','delegate_code_sha256','endpoint_before_sha256','status')):raise ValueError
            elif (value['reader_token'] is None or value['wait_seq'] is None or value['wait_payload_sha256'] is None
                  or value['delegate_code_sha256']!=contract.DELEGATE_CODES[runtime]['recv']):raise ValueError
        if value['phase']=='CALL' and (value['endpoint_after_sha256'] is not None or op=='RECV' and value['status'] is not None):raise ValueError
        if runtime not in {'cpython-3.12.3','cpython-3.12.15'} and any(value[k] is not None for k in ('endpoint_before_sha256','endpoint_after_sha256')):raise ValueError
    except (KeyError,TypeError,ValueError):raise ValueError('READY_ENDPOINT_EVENT_INVALID') from None


def validate_gap(value):
    try:
        binding(value,GAP_FIELDS,sensor.GAP_SCHEMA)
        if value['reason'] not in sensor.GAPS or value['point'] not in POINTS:raise ValueError
    except (KeyError,TypeError,ValueError):raise ValueError('READY_ENDPOINT_GAP_INVALID') from None


def validate_close(value):
    try:
        binding(value,CLOSE_FIELDS,sensor.CLOSE_SCHEMA)
        if not uint(value['cleared_readers']) or value['cleared_readers']>sensor.MAX_CALLS or value['selected_observer_closed'] is not True:raise ValueError
    except (KeyError,TypeError,ValueError):raise ValueError('READY_ENDPOINT_CLOSE_INVALID') from None


VALIDATORS={'READY_ENDPOINT':validate_event,'READY_ENDPOINT_GAP':validate_gap,'READY_ENDPOINT_CLOSE':validate_close}


def reference(records,row,seq_key,hash_key,event,point=None):
    value=row['payload'];seq=value[seq_key];target=records.get(seq)
    if (target is None or target['seq']>=row['seq'] or target['event']!=event
            or sensor.sha(sensor.canonical(target['payload']))!=value[hash_key]
            or point is not None and target['payload'].get('point')!=point):
        raise ValueError('READY_ENDPOINT_REFERENCE_SUBSTITUTED')
    return target


def journal(records,prepared,context):
    expected_session=sensor.sha(prepared['session'].encode());expected_process=sensor.sha(sensor.canonical(context))
    indexed={row['seq']:row for row in records};events=[];missing=[];groups={};closes=[]
    for row in records:
        if row['event'] not in VALIDATORS:continue
        value=row['payload'];VALIDATORS[row['event']](value)
        if value['session_sha256']!=expected_session or value['process_sha256']!=expected_process:
            raise ValueError('READY_ENDPOINT_CONTEXT_MISMATCH')
        if row['event']=='READY_ENDPOINT_GAP':missing.append('ready_endpoint_'+value['reason']);continue
        if row['event']=='READY_ENDPOINT_CLOSE':closes.append(row);continue
        if value['operation_finished_ns']>row['monotonic_ns']:raise ValueError('READY_ENDPOINT_CLOCK_INVALID')
        if value['operation']=='SEND':
            target=reference(indexed,row,'generation_seq','generation_payload_sha256','WORKER_READY_SEND')['payload']
            if any(target[k]!=value[k] for k in ('main_call_sha256','phase','status','operation_started_ns','exception_kind')):
                raise ValueError('READY_ENDPOINT_SEND_REFERENCE_MISMATCH')
            if target['operation']!='SEND' or target['pipe_call_sha256']!=value['call_sha256']:
                raise ValueError('READY_ENDPOINT_SEND_REFERENCE_MISMATCH')
        else:
            parent=reference(indexed,row,'parent_init_seq','parent_init_payload_sha256','INITIALIZATION','MultiprocExecutor._init_executor')['payload']
            if parent['phase']!='CALL':raise ValueError('READY_ENDPOINT_PARENT_REFERENCE_MISMATCH')
            if value['operation']=='RECV':
                wait=reference(indexed,row,'wait_seq','wait_payload_sha256','INITIALIZATION','WorkerProc.wait_for_ready')['payload']
                if wait['phase']!='CALL' or wait['parent_call_sha256']!=parent['call_sha256']:
                    raise ValueError('READY_ENDPOINT_WAIT_REFERENCE_MISMATCH')
        events.append(row);groups.setdefault((value['operation'],value['call_sha256']),[]).append(row)
    spans=[]
    for (operation,call),rows in groups.items():
        starts=[r for r in rows if r['payload']['phase']=='CALL'];ends=[r for r in rows if r['payload']['phase']!='CALL']
        if len(starts)!=1 or len(ends)!=1:
            missing.append('ready_endpoint_unique_call_pair_unavailable');continue
        begin,end=starts[0],ends[0];a,b=begin['payload'],end['payload']
        variable={'phase','operation_finished_ns','exception_kind','status','endpoint_after_sha256','generation_seq','generation_payload_sha256'}
        if operation=='CREATE':variable|={'reader_token','child_pid_claim','rank','start_method'}
        if begin['seq']>=end['seq'] or any(a[k]!=b[k] for k in FIELDS-variable):
            raise ValueError('READY_ENDPOINT_CALL_PAIR_MISMATCH')
        spans.append({'operation':operation,'call_sha256':call,'call_event_seq':begin['seq'],'terminal_event_seq':end['seq'],**b,
                      'same_endpoint_before_after':bool(b['endpoint_before_sha256'] and b['endpoint_before_sha256']==b['endpoint_after_sha256'])})
    if len(closes)!=1:missing.append('ready_endpoint_observer_close_unavailable_or_ambiguous')
    return {'events':events,'spans':spans,'missing':sorted(set(missing))}


def project(packet,prepared):
    if prepared.get('profile')!=sensor.PROFILE:raise ValueError('READY_ENDPOINT_PROFILE_UNSUPPORTED')
    result={'schema':'guard-ready-endpoint-projection/1','recording_profile':sensor.PROFILE,
        'events':[],'model_candidates':[],'generation_events':[],'generation_candidates':[],'ready_sends':[],
        'endpoint_events':[],'endpoint_candidates':[],'missing':[],**{k:False for k in TRUTH}}
    all_spans=[]
    for index,recording in enumerate((packet or {}).get('recordings',[])):
        capture=recording.get('capture') or {};records=capture.get('records',[])
        if not records:continue
        header=records[0]['payload'];context=header['context']
        if header.get('profile')!=sensor.PROFILE or header.get('schema')!=sensor.SCHEMA or header.get('session')!=prepared['session']:
            raise ValueError('READY_ENDPOINT_RECORDING_MISMATCH')
        data=journal(records,prepared,context);old=previous.journal(records,prepared,context);worker=startup.journal(records,prepared,context)
        bound=bool(capture.get('process_context_verified') and recording.get('session_owner_match') and recording.get('listed_file_identity_match') and capture.get('image_digest_verified'))
        identity={'recording':index,'pid':context['pid'],'start_ticks':context['start_ticks'],'process_binding_verified':bound}
        result['events'].extend({**identity,'event_seq':r['seq'],**r['payload']} for r in worker['events'])
        result['generation_events'].extend({**identity,'event_seq':r['seq'],**r['payload']} for r in old['events'])
        result['generation_candidates'].extend({**identity,**r} for r in old['candidates'])
        result['ready_sends'].extend({**identity,**r} for r in old['ready_sends'])
        for model in worker['model_candidates']:
            links=[r for r in old['candidates'] if r['worker_call_sha256']==model['worker_call_sha256']]
            result['model_candidates'].append({**identity,**model,'worker_generation_candidates':links,
                'local_worker_generation_model_candidate':bool(bound and model['same_startup_model_root_candidate'] and any(r['local_object_generation_candidate'] for r in links))})
        result['endpoint_events'].extend({**identity,'event_seq':r['seq'],**r['payload']} for r in data['events'])
        all_spans.extend({**identity,'context':context,**row} for row in data['spans'])
        result['missing']+=data['missing']+old['missing']+worker['missing']
    creators=[s for s in all_spans if s['operation']=='CREATE' and s['phase']=='RETURN' and s['endpoint_after_sha256']]
    receives=[s for s in all_spans if s['operation']=='RECV' and s['phase']=='RETURN' and s['status']=='READY' and s['same_endpoint_before_after']]
    sends=[s for s in all_spans if s['operation']=='SEND' and s['phase']=='RETURN' and s['status']=='READY' and s['same_endpoint_before_after']]
    for receive in receives:
        created=[c for c in creators if (c['recording'],c['reader_token'],c['parent_init_seq'],c['child_pid_claim'],c['rank'],c['endpoint_after_sha256'])
                  ==(receive['recording'],receive['reader_token'],receive['parent_init_seq'],receive['child_pid_claim'],receive['rank'],receive['endpoint_after_sha256'])]
        matching=[s for s in sends if s['endpoint_after_sha256']==receive['endpoint_after_sha256']]
        if len(created)!=1 or len(matching)!=1:
            result['missing'].append('ready_endpoint_unique_creator_sender_unavailable');continue
        creator,send=created[0],matching[0];contexts=(creator['context'],receive['context'],send['context'])
        domain=all(all(ctx.get(k)==contexts[0].get(k) and ctx.get(k) is not None for k in contract.DOMAIN_KEYS) for ctx in contexts)
        worker_links=[w for w in result['generation_candidates'] if w['recording']==send['recording'] and w.get('main_call_sha256')==send['main_call_sha256']]
        before_end=creator['operation_started_ns']<=send['operation_started_ns']<=send['operation_finished_ns']<=receive['operation_finished_ns']
        same_process=(creator['pid'],creator['start_ticks'])==(receive['pid'],receive['start_ticks'])
        unique_receives=sum(s['endpoint_after_sha256']==receive['endpoint_after_sha256'] for s in receives)==1
        candidate=bool(domain and same_process and before_end and unique_receives and creator['start_method']=='spawn'
                       and all(s['process_binding_verified'] for s in (creator,receive,send))
                       and send['pid']!=creator['pid'] and send['pid']==creator['child_pid_claim']
                       and send['rank']==creator['rank'] and send['start_ticks']>0
                       and any(w['local_object_generation_candidate'] for w in worker_links))
        result['endpoint_candidates'].append({'parent_recording':receive['recording'],'child_recording':send['recording'],
            'creator_call_sha256':creator['call_sha256'],'receiver_call_sha256':receive['call_sha256'],
            'sender_call_sha256':send['call_sha256'],'main_call_sha256':send['main_call_sha256'],
            'reader_token':receive['reader_token'],'endpoint_sha256':receive['endpoint_after_sha256'],
            'same_held_fifo_candidate':candidate,'namespace_and_clock_domain_match':bool(domain),
            'child_PID_claim_matches_recorded_process':send['pid']==creator['child_pid_claim'],**{k:False for k in TRUTH}})
    result['missing']=sorted(set(result['missing']+['actual_worker_producer_authentication_unverified',
        'endpoint_candidate_is_not_service_admission_or_full_readiness','failure_repair_lineage_unverified']))
    return result
