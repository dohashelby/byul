"""Closed source-owned native24 control facts; receipts alone select nothing."""
from __future__ import annotations
from . import vllm_fault_sensor as sensor,vllm_fault_control as control,vllm_rank_failure_sensor as rank
from . import vllm_rank_failure_metadata as rank_metadata

PHASES=frozenset({'ASSIGNED','READY','ACTIVATE','RELEASE','EXPIRED','EXCEPTION_INTENT','EXCEPTION_CAUGHT'})
FIELDS=frozenset({'schema','phase','point','source_module','source_sha256','body_code_sha256',
 'observer_runtime','observer_code_sha256','nonce','fault_kind','rank','external_request_sha256',
 'native_request_sha256','session','target_sha256','asgi_invocation_sha256','call_sha256',
 'ready_receipt_sha256','action_receipt_sha256','operation_started_ns','observation_ns',
 'barrier_deadline_ns','clock_context','rpc_active','runner_bound','async_output_bound','worker_loop_observed'})
POINT_MODULES={'InputProcessor.assign_request_id':sensor.INPUT,'WorkerProc._execute_worker_rpc':sensor.MP,
 'WorkerProc.enqueue_output':sensor.MP}


def validate_control(v):
 try:
  if(type(v)is not dict or set(v)!=FIELDS or v['schema']!=sensor.EVENT_SCHEMA
   or v['phase']not in PHASES or v['point']not in POINT_MODULES or v['source_module']!=POINT_MODULES[v['point']]
   or v['source_sha256']!=({**control_source(),**sensor.SOURCE_ADDITIONS})[v['source_module']]
   or v['observer_runtime']not in sensor.RUNTIME_CODE_CONTRACTS
   or v['observer_code_sha256']!=sensor.RUNTIME_CODE_CONTRACTS[v['observer_runtime']]
   or v['fault_kind']not in control.KINDS or type(v['rank'])is not int or v['rank']!=0):raise ValueError
  for key,size in (('nonce',32),('session',32),('target_sha256',64),('external_request_sha256',64),
    ('native_request_sha256',64),('body_code_sha256',64)):
   if not control.fingerprint(v[key],size):raise ValueError
  for key in ('asgi_invocation_sha256','call_sha256','ready_receipt_sha256','action_receipt_sha256'):
   if v[key]is not None and not control.fingerprint(v[key]):raise ValueError
  control.clock_context(v['clock_context'])
  if type(v['observation_ns'])is not int or not 0<v['observation_ns']<2**64:raise ValueError
  for key in ('rpc_active','runner_bound','async_output_bound','worker_loop_observed'):
   if type(v[key])is not bool:raise ValueError
  if v['phase']=='ASSIGNED':
   if(v['point']!='InputProcessor.assign_request_id'or v['asgi_invocation_sha256']is None
    or any(v[k]is not None for k in ('operation_started_ns','barrier_deadline_ns','ready_receipt_sha256','action_receipt_sha256'))
    or any(v[k]for k in ('rpc_active','runner_bound','async_output_bound','worker_loop_observed'))):raise ValueError
  else:
   if v['point']=='InputProcessor.assign_request_id'or v['call_sha256']is None:raise ValueError
   for key in ('operation_started_ns','barrier_deadline_ns'):
    if type(v[key])is not int or not 0<v[key]<2**64:raise ValueError
   if v['operation_started_ns']>v['observation_ns']or v['barrier_deadline_ns']<=v['operation_started_ns']:raise ValueError
   if v['phase']=='READY'and(v['ready_receipt_sha256']is None or not v['observation_ns']<v['barrier_deadline_ns']):raise ValueError
   if v['point']=='WorkerProc._execute_worker_rpc':
    if not v['rpc_active']or not v['runner_bound']or v['async_output_bound']or v['worker_loop_observed']or v['fault_kind']=='communication-stall':raise ValueError
   else:
    if v['rpc_active']or not v['runner_bound']or not v['async_output_bound']or not v['worker_loop_observed']or v['fault_kind']!='communication-stall':raise ValueError
   if v['phase']=='ACTIVATE'and(v['action_receipt_sha256']is None or v['fault_kind']not in {'exception','communication-stall'}):raise ValueError
   if v['phase']in {'EXCEPTION_INTENT','EXCEPTION_CAUGHT'}and v['fault_kind']!='exception':raise ValueError
 except (ValueError,KeyError,TypeError):raise ValueError('FAULT_NATIVE_CONTROL_SAMPLE_INVALID')from None


def control_source():return {sensor.INPUT:'d79601e4a053a7bacb0d6a23e273348435e4ab70c6a315dcccb2d428ed57d96b'}


def validate_rank(v):
 return rank_metadata.validate_sample(v,runtime_contracts=sensor.RANK_CODE_CONTRACTS)
