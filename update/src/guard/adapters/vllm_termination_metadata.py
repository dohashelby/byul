"""Closed native23 facts. Whole request completion requires every scoped proof."""
from __future__ import annotations
import re
from . import vllm_termination_sensor as sensor

PHASES=frozenset({'CALL','RETURN','RAISE','FENCE_RECORDED','FENCE_COMPLETE','FENCE_PENDING','FENCE_TIMEOUT','FENCE_ERROR','OUTPUT_CREATED','OUTPUT_RELEASED','ASGI_DISCONNECT_RECEIVED','ASGI_BODY_SEND_RETURN','ASGI_APP_RETURN','ASGI_APP_RAISE','STATE_SNAPSHOT','OBSERVER_GAP'})
FIELDS=frozenset({'schema','point','phase','source_module','source_sha256','body_code_sha256','observer_runtime','observer_code_sha256','call_sha256','operation_started_ns','operation_finished_ns','request_sha256','gpu_work_selected_sha256','states','scheduler_before','scheduler_after','stream_kind','stream_sha256','stream_set_complete','caller_stream_bound','output_token','error_kind','asgi_request_sha256','asgi_invocation_sha256','asgi_path_sha256'})
SCHEDULER_FIELDS=frozenset({'request_sha256','request_present','source_request_type_verified','last_sched_seq','num_computed_tokens','num_preemptions','num_in_flight_tokens','streaming'})
STATE_FIELDS=frozenset({'request_sha256','worker_slot_present','worker_mapping_complete'})
POINT_MODULES={c+'.'+m:module for module,classes in sensor.TARGETS.items()for c,methods in classes.items()for m in methods}
POINT_MODULES['ASGI.request']='observer.asgi'


def fingerprint(value):return type(value)is str and re.fullmatch('[0-9a-f]{64}',value)is not None


def validate_sample(value):
    try:
        if (type(value)is not dict or set(value)!=FIELDS or value['schema']!=sensor.EVENT_SCHEMA
                or value['point']not in POINT_MODULES or value['source_module']!=POINT_MODULES[value['point']]
                or value['phase']not in PHASES or value['observer_runtime']not in sensor.RUNTIME_CODE_CONTRACTS
                or not fingerprint(value['observer_code_sha256'])or value['observer_code_sha256']!=sensor.RUNTIME_CODE_CONTRACTS[value['observer_runtime']]
                or not fingerprint(value['call_sha256'])or not fingerprint(value['body_code_sha256'])
                or not fingerprint(value['source_sha256'])or type(value['stream_set_complete'])is not bool or type(value['caller_stream_bound'])is not bool):raise ValueError
        if value['source_module']!='observer.asgi'and value['source_sha256']!=sensor.SOURCE_ADDITIONS[value['source_module']]:raise ValueError
        if type(value['operation_started_ns'])is not int or not 0<=value['operation_started_ns']<2**63:raise ValueError
        if value['phase']=='CALL':
            if value['operation_finished_ns']is not None:raise ValueError
        elif type(value['operation_finished_ns'])is not int or not value['operation_started_ns']<=value['operation_finished_ns']<2**63:raise ValueError
        ids=value['request_sha256']
        if type(ids)is not list or len(ids)>8 or any(not fingerprint(v)for v in ids)or len(set(ids))!=len(ids):raise ValueError
        work=value['gpu_work_selected_sha256']
        if type(work)is not list or len(work)>8 or len(set(work))!=len(work)or any(v not in ids for v in work):raise ValueError
        if work and value['point']not in {'GPUModelRunner.execute_model','GPUModelRunner.sample_tokens'}:raise ValueError
        if value['phase']in {'FENCE_RECORDED','FENCE_COMPLETE','FENCE_PENDING','FENCE_TIMEOUT'}and work!=ids:raise ValueError
        if type(value['states'])is not list or len(value['states'])>8:raise ValueError
        seen=set()
        for state in value['states']:
            if(type(state)is not dict or set(state)!=STATE_FIELDS or state['request_sha256']not in ids or state['request_sha256']in seen
                    or type(state['worker_mapping_complete'])is not bool or state['worker_slot_present']is not None and type(state['worker_slot_present'])is not bool
                    or state['worker_mapping_complete']!=(type(state['worker_slot_present'])is bool)):raise ValueError
            seen.add(state['request_sha256'])
        for key in ('scheduler_before','scheduler_after'):
            rows=value[key]
            if type(rows)is not list or len(rows)>8 or rows and not value['point'].startswith('Scheduler.'):raise ValueError
            seen=set()
            for state in rows:
                if type(state)is not dict or set(state)!=SCHEDULER_FIELDS:raise ValueError
                if state['request_sha256']not in ids or state['request_sha256']in seen:raise ValueError
                seen.add(state['request_sha256'])
                if type(state['request_present'])is not bool or type(state['source_request_type_verified'])is not bool:raise ValueError
                for k in ('last_sched_seq','num_computed_tokens','num_preemptions','num_in_flight_tokens'):
                    if state[k]is not None and (type(state[k])is not int or not 0<=state[k]<2**63):raise ValueError
                if state['streaming']is not None and type(state['streaming'])is not bool:raise ValueError
        if value['stream_kind']not in (None,'main','copy'):raise ValueError
        if(value['stream_kind']is None)!=(value['stream_sha256']is None):raise ValueError
        if value['stream_sha256']is not None and not fingerprint(value['stream_sha256']):raise ValueError
        if value['phase']in {'FENCE_RECORDED','FENCE_COMPLETE','FENCE_PENDING','FENCE_TIMEOUT'}and value['stream_kind']is None:raise ValueError
        if value['stream_set_complete']and(value['stream_kind']is None or value['caller_stream_bound']is not True):raise ValueError
        if value['output_token']is not None and not fingerprint(value['output_token']):raise ValueError
        if value['phase']in {'OUTPUT_CREATED','OUTPUT_RELEASED'}and(value['point']!='AsyncOutput.__init__'or value['output_token']is None):raise ValueError
        if value['error_kind']not in (None,'native-exception','observer-bound','stream-unavailable'):raise ValueError
        for key in ('asgi_request_sha256','asgi_invocation_sha256','asgi_path_sha256'):
            if value[key]is not None and not fingerprint(value[key]):raise ValueError
        if value['point']=='ASGI.request':
            if not value['phase'].startswith('ASGI_')or ids or value['states']:raise ValueError
        elif value['phase'].startswith('ASGI_')or any(value[k]is not None for k in ('asgi_request_sha256','asgi_invocation_sha256','asgi_path_sha256')):raise ValueError
    except (KeyError,TypeError,ValueError):raise ValueError('REQUEST_TERMINATION_SAMPLE_INVALID')from None
