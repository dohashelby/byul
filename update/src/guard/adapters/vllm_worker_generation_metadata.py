"""Typed local Worker generation and original selected-pipe claims for LIF09."""
import re
from . import vllm_worker_generation_sensor as sensor,vllm_worker_generation_contract as contract
from . import vllm_worker_startup_sensor as startup,vllm_worker_startup_metadata as previous
from . import vllm_worker_startup_contract as previous_contract

BINDING=frozenset({'session_sha256','process_sha256','observer_runtime','observer_code_sha256'})
TRUTH=frozenset(sensor.TRUTH)
FIELDS=frozenset({'schema','point','phase','call_sha256','parent_call_sha256','main_call_sha256','constructor_call_sha256',
 'owner_token','worker_token','rank','local_rank','source_module','source_sha256','body_code_sha256','entry_code_sha256','entry_kind',
 'source_verified','operation_started_ns','operation_finished_ns','exception_kind'})|BINDING|TRUTH
LINK_FIELDS=frozenset({'schema','constructor_call_sha256','constructor_event_seq','wrapper_call_sha256','wrapper_event_seq',
 'worker_token','owner_token','main_call_sha256','rank','local_rank','worker_event_seq','worker_payload_sha256','worker_call_sha256','phase'})|BINDING|TRUTH
PIPE_FIELDS=frozenset({'schema','main_call_sha256','pipe_token','pipe_call_sha256','operation','phase','status','delegate_code_sha256',
 'delegate_code_verified','operation_started_ns','operation_finished_ns','exception_kind'})|BINDING|TRUTH
GAP_FIELDS=frozenset({'schema','reason','point'})|BINDING|TRUTH
CLOSE_FIELDS=frozenset({'schema','cleared_local_worker_bindings','local_observer_closed'})|BINDING|TRUTH
PHASES=frozenset({'CALL','RETURN','RAISE'})


def fingerprint(v):return type(v) is str and re.fullmatch('[0-9a-f]{64}',v) is not None
def uint(v):return type(v) is int and 0<=v<2**63
def optional_hash(v):return v is None or fingerprint(v)
def rank(v):return v is None or type(v) is int and 0<=v<=63


def common(value,fields,schema):
    if type(value) is not dict or set(value)!=fields or value['schema']!=schema:raise ValueError
    runtime=value['observer_runtime']
    if runtime not in sensor.RUNTIME_CODE_CONTRACTS or value['observer_code_sha256']!=sensor.RUNTIME_CODE_CONTRACTS[runtime]:raise ValueError
    if not all(fingerprint(value[k]) for k in ('session_sha256','process_sha256')) or any(value[k] is not False for k in TRUTH):raise ValueError


def times(value):
    if value['phase'] not in PHASES or not all(uint(value[k]) for k in ('operation_started_ns','operation_finished_ns')):raise ValueError
    if value['operation_started_ns']>value['operation_finished_ns']:raise ValueError
    if value['phase']=='RAISE':
        if value['exception_kind'] not in {'exception','base-exception'}:raise ValueError
    elif value['exception_kind'] is not None:raise ValueError


def validate_event(value):
    try:
        common(value,FIELDS,sensor.EVENT_SCHEMA);times(value)
        point=value['point'];name=next((n for n,points in contract.TARGETS.items() if point in points),None)
        if name is None or value['source_module']!=name or value['source_sha256']!=contract.SOURCE_SHA256[name]:raise ValueError
        if not fingerprint(value['call_sha256']) or not all(optional_hash(value[k]) for k in ('parent_call_sha256','main_call_sha256','constructor_call_sha256','owner_token','worker_token')):raise ValueError
        if not rank(value['rank']) or not rank(value['local_rank']) or type(value['source_verified']) is not bool:raise ValueError
        expected=contract.POINT_CODES[value['observer_runtime']]
        if value['body_code_sha256']!=expected[point] or value['entry_kind'] not in {'direct-body','otel-sync-wrapper'}:raise ValueError
        entry=expected['otel-sync-wrapper'] if value['entry_kind']=='otel-sync-wrapper' else expected[point]
        if value['entry_code_sha256']!=entry or point=='WorkerProc.worker_main' and value['entry_kind']!='direct-body':raise ValueError
        if point=='WorkerProc.worker_main':
            if value['main_call_sha256']!=value['call_sha256'] or value['constructor_call_sha256'] is not None or value['owner_token'] is not None or value['worker_token'] is not None:raise ValueError
        if point=='WorkerProc.__init__' and (value['constructor_call_sha256']!=value['call_sha256'] or not fingerprint(value['owner_token'])):raise ValueError
    except (KeyError,TypeError,ValueError,StopIteration):raise ValueError('WORKER_GENERATION_EVENT_INVALID') from None


def validate_link(value):
    try:
        common(value,LINK_FIELDS,sensor.LINK_SCHEMA)
        for key in ('constructor_call_sha256','wrapper_call_sha256','worker_token','owner_token','worker_payload_sha256','worker_call_sha256'):
            if not fingerprint(value[key]):raise ValueError
        if not optional_hash(value['main_call_sha256']) or value['phase'] not in PHASES:raise ValueError
        if not all(uint(value[k]) for k in ('constructor_event_seq','wrapper_event_seq','worker_event_seq')) or not rank(value['rank']) or not rank(value['local_rank']):raise ValueError
    except (KeyError,TypeError,ValueError):raise ValueError('WORKER_GENERATION_LINK_INVALID') from None


def validate_pipe(value):
    try:
        common(value,PIPE_FIELDS,sensor.PIPE_SCHEMA);times(value)
        if not all(fingerprint(value[k]) for k in ('main_call_sha256','pipe_token','pipe_call_sha256')):raise ValueError
        if value['operation'] not in {'SEND','CLOSE'} or value['status'] not in {None,'READY','other'}:raise ValueError
        if value['operation']=='CLOSE' and value['status'] is not None:raise ValueError
        expected=contract.CONNECTION_CODES[value['observer_runtime']]['send' if value['operation']=='SEND' else 'close']
        if value['delegate_code_verified'] is not True or value['delegate_code_sha256']!=expected:raise ValueError
    except (KeyError,TypeError,ValueError):raise ValueError('WORKER_GENERATION_PIPE_INVALID') from None


def validate_gap(value):
    try:
        common(value,GAP_FIELDS,sensor.GAP_SCHEMA)
        if value['reason'] not in sensor.GAPS or value['point'] not in contract.POINTS|set(previous_contract.SPAN_NAMES):raise ValueError
    except (KeyError,TypeError,ValueError):raise ValueError('WORKER_GENERATION_GAP_INVALID') from None


def validate_close(value):
    try:
        common(value,CLOSE_FIELDS,sensor.CLOSE_SCHEMA)
        if value['local_observer_closed'] is not True or not uint(value['cleared_local_worker_bindings']) or value['cleared_local_worker_bindings']>sensor.MAX_CALLS:raise ValueError
    except (KeyError,TypeError,ValueError):raise ValueError('WORKER_GENERATION_CLOSE_INVALID') from None


VALIDATORS={'WORKER_GENERATION':validate_event,'WORKER_GENERATION_LINK':validate_link,
 'WORKER_READY_SEND':validate_pipe,'WORKER_GENERATION_GAP':validate_gap,'WORKER_GENERATION_CLOSE':validate_close}


def journal(records,prepared,context):
    events=[];links=[];pipes=[];gaps=[];closes=[];missing=[]
    expected_session=sensor.sha(prepared['session'].encode());expected_process=sensor.sha(sensor.canonical(context))
    by_seq={r['seq']:r for r in records};groups={};pipe_groups={};runtimes=set()
    for row in records:
        if row['event'] not in VALIDATORS:continue
        value=row['payload'];VALIDATORS[row['event']](value);runtimes.add(value['observer_runtime'])
        if value['session_sha256']!=expected_session or value['process_sha256']!=expected_process:raise ValueError('WORKER_GENERATION_CONTEXT_MISMATCH')
        if row['event'] in {'WORKER_GENERATION','WORKER_READY_SEND'} and value['operation_finished_ns']>row['monotonic_ns']:
            raise ValueError('WORKER_GENERATION_CLOCK_BINDING_INVALID')
        if row['event']=='WORKER_GENERATION':
            events.append(row);groups.setdefault(value['call_sha256'],[]).append(row)
        elif row['event']=='WORKER_GENERATION_LINK':links.append(row)
        elif row['event']=='WORKER_READY_SEND':
            pipes.append(row);pipe_groups.setdefault(value['pipe_call_sha256'],[]).append(row)
        elif row['event']=='WORKER_GENERATION_GAP':gaps.append(row);missing.append('worker_generation_'+value['reason'])
        else:closes.append(row)
    if len(runtimes)>1:raise ValueError('WORKER_GENERATION_RUNTIME_MISMATCH')
    previous_runtimes={r['payload']['observer_runtime'] for r in records if r['event']=='WORKER_STARTUP'}
    if runtimes and previous_runtimes and runtimes!=previous_runtimes:raise ValueError('WORKER_GENERATION_RUNTIME_MISMATCH')
    calls=[];indexed={}
    for token,rows in groups.items():
        first=[r for r in rows if r['payload']['phase']=='CALL'];last=[r for r in rows if r['payload']['phase']!='CALL']
        begin=first[0] if len(first)==1 else None;end=last[0] if len(last)==1 else None
        stable=FIELDS-{'phase','worker_token','operation_finished_ns','exception_kind','source_verified'}
        paired=bool(len(rows)==2 and begin and end and begin['seq']<end['seq']
            and all(begin['payload'][k]==end['payload'][k] for k in stable)
            and begin['payload']['operation_finished_ns']<=end['payload']['operation_finished_ns']<=end['monotonic_ns'])
        value=begin['payload'] if begin else rows[0]['payload'];active=bool(begin and not last and len(rows)==1 and value['point']=='WorkerProc.worker_main')
        data={'call_sha256':token,'point':value['point'],'begin':begin,'end':end,'paired':paired,
            'source_entry_observed':bool(begin and value['source_verified']),'main_active_or_end_unobserved':active}
        indexed[token]=data;calls.append(data)
        if not paired and not active:missing.append('worker_generation_unique_call_pair_unavailable')
        if active:missing.append('worker_main_terminal_not_observed')
    bindings={}
    for row in events:
        value=row['payload']
        if value['point']=='WorkerWrapperBase.init_worker' and value['phase']=='RETURN' and value['worker_token'] is not None:
            bindings.setdefault(value['constructor_call_sha256'],set()).add((value['call_sha256'],value['worker_token']))
    candidates=[]
    for row in links:
        value=row['payload'];target=by_seq.get(value['worker_event_seq']);ctor=indexed.get(value['constructor_call_sha256']);wrapper=indexed.get(value['wrapper_call_sha256'])
        ctor_row=by_seq.get(value['constructor_event_seq']);wrapper_row=by_seq.get(value['wrapper_event_seq'])
        if not (target and target['event']=='WORKER_STARTUP' and row['seq']==target['seq']+1
                and sensor.sha(sensor.canonical(target['payload']))==value['worker_payload_sha256']
                and target['payload']['call_sha256']==value['worker_call_sha256'] and target['payload']['phase']==value['phase']
                and ctor_row and ctor_row['event']=='WORKER_GENERATION' and ctor_row['payload']['point']=='WorkerProc.__init__' and ctor_row['payload']['phase']=='CALL'
                and wrapper_row and wrapper_row['event']=='WORKER_GENERATION' and wrapper_row['payload']['point']=='WorkerWrapperBase.init_worker' and wrapper_row['payload']['phase']=='RETURN'):
            raise ValueError('WORKER_GENERATION_LINK_SUBSTITUTED')
        cp,wp=ctor_row['payload'],wrapper_row['payload']
        if (cp['call_sha256']!=value['constructor_call_sha256'] or wp['call_sha256']!=value['wrapper_call_sha256']
                or wp['parent_call_sha256']!=cp['call_sha256'] or wp['constructor_call_sha256']!=cp['call_sha256']
                or any(wp[k]!=value[k] or cp[k]!=value[k] for k in ('owner_token','main_call_sha256','rank','local_rank'))
                or wp['worker_token']!=value['worker_token'] or not ctor_row['seq']<wrapper_row['seq']<target['seq']):
            raise ValueError('WORKER_GENERATION_OBJECT_LINK_INVALID')
        if ctor and ctor['end'] and ctor['end']['payload']['phase']=='RAISE' and target['seq']>ctor['end']['seq']:
            raise ValueError('WORKER_GENERATION_LINK_AFTER_FAILED_CONSTRUCTOR')
        main=indexed.get(value['main_call_sha256'])
        if main and main['end'] and target['seq']>main['end']['seq']:raise ValueError('WORKER_GENERATION_LINK_AFTER_MAIN_END')
        local=bool(ctor and wrapper and ctor['paired'] and wrapper['paired'] and cp['source_verified'] and wp['source_verified']
            and len(bindings.get(value['constructor_call_sha256'],set()))==1)
        candidates.append({**value,'link_event_seq':row['seq'],'local_object_generation_candidate':local,
            'constructor_return_observed':bool(ctor and ctor['paired'] and ctor['end']['payload']['phase']=='RETURN')})
    ready=[]
    for token,rows in pipe_groups.items():
        first=[r for r in rows if r['payload']['phase']=='CALL'];last=[r for r in rows if r['payload']['phase']!='CALL']
        begin=first[0] if len(first)==1 else None;end=last[0] if len(last)==1 else None
        stable=PIPE_FIELDS-{'phase','operation_finished_ns','exception_kind'}
        paired=bool(len(rows)==2 and begin and end and begin['seq']<end['seq']
            and all(begin['payload'][k]==end['payload'][k] for k in stable)
            and begin['payload']['operation_finished_ns']<=end['payload']['operation_finished_ns']<=end['monotonic_ns'])
        value=begin['payload'] if begin else rows[0]['payload'];main=indexed.get(value['main_call_sha256'])
        selected=bool(main and main['point']=='WorkerProc.worker_main' and main['source_entry_observed'] and main['begin']['seq']<rows[0]['seq'])
        ready.append({'pipe_call_sha256':token,'main_call_sha256':value['main_call_sha256'],'pipe_token':value['pipe_token'],
            'operation':value['operation'],'status':value['status'],'start_seq':begin['seq'] if begin else None,'end_seq':end['seq'] if end else None,
            'terminal_phase':end['payload']['phase'] if end else None,'exception_kind':end['payload']['exception_kind'] if end else None,
            'paired':paired,'source_main_entry_observed':selected,
            'original_send_return_observed':bool(paired and selected and value['operation']=='SEND' and end['payload']['phase']=='RETURN'),
            'original_send_raise_observed':bool(paired and selected and value['operation']=='SEND' and end['payload']['phase']=='RAISE'),
            'original_close_return_observed':bool(paired and selected and value['operation']=='CLOSE' and end['payload']['phase']=='RETURN'),
            'original_close_raise_observed':bool(paired and selected and value['operation']=='CLOSE' and end['payload']['phase']=='RAISE'),**{k:False for k in TRUTH}})
        if not paired:missing.append('worker_ready_pipe_unique_call_pair_unavailable')
    if not events:missing.append('worker_generation_observation_unavailable')
    if not links:missing.append('worker_generation_object_link_unavailable')
    if not any(r['operation']=='SEND' for r in ready):missing.append('worker_selected_ready_send_unavailable')
    if len(closes)!=1:missing.append('worker_generation_observer_close_unavailable_or_ambiguous')
    return {'events':events,'calls':calls,'links':links,'candidates':candidates,'pipe_events':pipes,'ready_sends':ready,
        'close_events':closes,'gap_events':gaps,'missing':sorted(set(missing))}


def project(packet,prepared):
    if prepared.get('profile')!=sensor.PROFILE:raise ValueError('WORKER_GENERATION_PROFILE_UNSUPPORTED')
    result={'schema':'guard-worker-generation-projection/1','recording_profile':sensor.PROFILE,
        'events':[],'model_candidates':[],'generation_events':[],'generation_candidates':[],'ready_sends':[],'missing':[],**{k:False for k in TRUTH}}
    for index,recording in enumerate((packet or {}).get('recordings',[])):
        capture=recording.get('capture') or {};records=capture.get('records',[])
        if not records:continue
        header=records[0]['payload']
        if header.get('profile')!=sensor.PROFILE or header.get('schema')!=sensor.SCHEMA or header.get('session')!=prepared['session']:
            raise ValueError('WORKER_GENERATION_RECORDING_MISMATCH')
        context=header['context'];data=journal(records,prepared,context);old=previous.journal(records,prepared,context)
        bound=bool(capture.get('process_context_verified') and recording.get('session_owner_match') and recording.get('listed_file_identity_match'))
        identity={'recording':index,'pid':context['pid'],'start_ticks':context['start_ticks'],'process_binding_verified':bound}
        result['events'].extend({**identity,'event_seq':r['seq'],**r['payload']} for r in old['events'])
        result['generation_events'].extend({**identity,'event_seq':r['seq'],**r['payload']} for r in data['events'])
        result['generation_candidates'].extend({**identity,**r} for r in data['candidates'])
        result['ready_sends'].extend({**identity,**r} for r in data['ready_sends'])
        for model in old['model_candidates']:
            links=[r for r in data['candidates'] if r['worker_call_sha256']==model['worker_call_sha256']]
            result['model_candidates'].append({**identity,**model,'worker_generation_candidates':links,
                'local_worker_generation_model_candidate':bool(bound and model['same_startup_model_root_candidate'] and any(r['local_object_generation_candidate'] for r in links))})
        result['missing']+=data['missing']+old['missing']
    result['missing']=sorted(set(result['missing']+['actual_worker_and_parent_cohort_generation_unverified',
        'READY_send_return_is_not_receiver_or_service_admission','failure_repair_lineage_and_full_preparation_unverified']))
    return result
