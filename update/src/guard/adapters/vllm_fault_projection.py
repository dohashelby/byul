"""Strict native24 request/rank fault joins; missing scope stays UNKNOWN."""
from __future__ import annotations
import hashlib,json
from pathlib import Path
from . import vllm_fault_control as control,vllm_fault_sensor as sensor,vllm_fault_metadata as metadata
from .vllm_termination_projection import retired_import_only

FLAGS=('occurrence_verified','request_fault_overlap_verified','detection_deadline_verified',
 'upper_notification_deadline_verified','affected_request_error_verified','clock_verified','whole_kind_complete')
SCOPE=frozenset({'source_profile','session','target_sha256','pod_uid','container_id',
 'external_request_sha256','nonce','fault_kind','rank'})


def _clock(context):
 value={k:context[k]for k in control.CLOCK_FIELDS};control.clock_context(value);return value


def _active_inventory(packet,scope):
 from guard.executors.lifecycle_session import parse_inventory,_complete
 sides=[]
 if packet.get('python_processes_without_recording'):return False
 for key in ('before','after'):
  snapshot=packet[key];m=snapshot['metadata']
  parse_inventory(json.dumps(m,sort_keys=True).encode(),scope['session'])
  if not _complete(snapshot)or m['sealed']is not False:return False
  sides.append(m)
 a,b=sides
 if a['processes']!=b['processes']:return False
 for key in ('device','inode','uid','mode'):
  if a['directory_before'][key]!=b['directory_after'][key]:return False
 files=lambda m:{(r['pid'],r['start_ticks']):r for r in m['files']}
 first,last=files(a),files(b)
 live_python={(p['pid'],p['start_ticks'])for p in a['processes']if p['same_executable']}
 if not live_python<=set(first):return False
 if set(first)!=set(last)or set(first)!={(r['pid'],r['start_ticks'])for r in packet['recordings']}:return False
 for item in packet['recordings']:
  key=(item['pid'],item['start_ticks']);x,y=first[key],last[key];c=item.get('capture')or{};f=c.get('provenance',{}).get('file_identity')or{}
  if(x['regular']is not True or x['uid']!=a['directory_before']['uid']or x['mode']&0o077
   or any(x[k]!=y[k]for k in ('device','inode','uid','mode','regular'))
   or not x['bytes']<=f.get('size',-1)<=y['bytes']or any(f.get(k)!=x[k]for k in ('device','inode'))):return False
 return True


def _active_binding(c,context):
 value=c.get('active_prefix_binding');provenance=c.get('provenance')or{};file=provenance.get('file_identity')or{}
 keys={'schema','context_sha256','capture_sha256','bytes','later_capture_sha256','later_bytes','file_device','file_inode','process_context_identity_verified','prefix_extension_verified'}
 return bool(type(value)is dict and set(value)==keys and value['schema']=='guard-live-lifecycle-prefix/1'
  and value['process_context_identity_verified']is True and value['prefix_extension_verified']is True
  and value['context_sha256']==hashlib.sha256(json.dumps(context,sort_keys=True,separators=(',',':')).encode()).hexdigest()
  and value['capture_sha256']==provenance.get('capture_sha256')and value['bytes']==provenance.get('bytes')
  and type(value['later_bytes'])is int and value['later_bytes']>=value['bytes']
  and control.fingerprint(value['later_capture_sha256'])and all(type(value['file_'+k])is int and value['file_'+k]>0 and value['file_'+k]==file.get(k)for k in ('device','inode')))


def _rows(packet,scope,previous=None,*,active=False):
 if(type(packet)is not dict or packet.get('schema')!='guard-lifecycle-session-capture/1'
  or packet.get('session')!=scope['session']or type(packet.get('recordings'))is not list
  or not 0<len(packet['recordings'])<=32):raise ValueError
 owner=packet['before']['owner']
 if(not owner or owner.get('pod_uid')!=scope['pod_uid']or owner.get('container_id')!=scope['container_id']
  or owner!=packet['after']['owner']or packet['before'].get('owner_stable')is not True or packet['after'].get('owner_stable')is not True):raise ValueError
 result=[];physical={};coverage=True;previous=previous or{}
 for index,item in enumerate(packet['recordings']):
  c=item.get('capture')or{};rows=c.get('records')or[]
  if not rows or len(rows)>sensor.MAX_ROWS:raise ValueError
  from guard.executors.lifecycle_capture import parse_recording,canonical_row
  from . import vllm_lifecycle_sensor as base8
  settings={'schema':base8.CONFIG_SCHEMA,'session':scope['session'],'output_dir':'/guard-lifecycle/data/'+scope['session'],
   'control_path':'/guard-lifecycle/data/'+scope['session']+'/SEALED'}
  settings_raw=(json.dumps(settings,sort_keys=True,indent=2)+'\n').encode()
  prepared={'profile':sensor.PROFILE,'session':scope['session'],'fault_control_target_sha256':scope['target_sha256'],
   'assets':{'settings.json':{'sha256':hashlib.sha256(settings_raw).hexdigest()},
    'guard_lifecycle_sensor.py':{'sha256':hashlib.sha256(Path(sensor.__file__).read_bytes()).hexdigest()}}}
  raw=b''.join(canonical_row(row)for row in rows)
  if(c.get('provenance',{}).get('capture_sha256')!=hashlib.sha256(raw).hexdigest()
   or c.get('provenance',{}).get('bytes')!=len(raw)):raise ValueError
  parsed,parsed_summary=parse_recording(raw,prepared)
  if parsed!=rows or any((c.get('summary')or{}).get(k)!=parsed_summary[k]for k in ('recording_closed','recording_end','incomplete_final_row','discarded_tail_bytes')):raise ValueError
  header=rows[0]['payload'];context=header['context'];key=(context['pid'],context['start_ticks'])
  if(header.get('profile')!=sensor.PROFILE or header.get('session')!=scope['session']or rows[0].get('event')!='START'
   or not control.fingerprint(c.get('provenance',{}).get('capture_sha256'))):raise ValueError
  _clock(context)
  if key in physical:raise ValueError
  owned=bool(item.get('session_owner_match')is True and item.get('listed_file_identity_match')is True
   and c.get('image_digest_verified')is True and c.get('provenance',{}).get('owner')==owner)
  active_bound=bool(active and item.get('session_owner_match')is True and c.get('image_digest_verified')is True and c.get('provenance',{}).get('owner')==owner and _active_binding(c,context))
  live=bool(owned and c.get('process_context_verified')is True or active_bound)
  summary=c.get('summary')or{};ended=rows[-1].get('event')=='END'and rows[-1].get('payload')in ({'reason':'SEALED'},{'reason':'PROCESS_EXIT'})
  closed=bool(summary.get('recording_closed')is True and summary.get('incomplete_final_row')is False
   and summary.get('discarded_tail_bytes')==0 and ended and summary.get('recording_end')==rows[-1]['payload']['reason'])
  old=previous.get(key)
  postmortem=bool(owned and old is not None and old['physical']and old['context']==context and closed
   and rows[-1]['payload']=={'reason':'PROCESS_EXIT'}and c.get('process_recheck')=={
    'pid':key[0],'start_ticks':key[1],'recording_end':'PROCESS_EXIT','recording_closed':True,'result':'process_exited'})
  # SIGKILL cannot run a Python END writer. Keep this open-recording fact and
  # associate only an exact, stable extension of an independently live prefix.
  # The final occurrence/monitor checks below must corroborate every such role.
  old_rows=((old or{}).get('item',{}).get('capture')or{}).get('records')or[]
  old_file=(((old or{}).get('item',{}).get('capture')or{}).get('provenance')or{}).get('file_identity')or{}
  new_file=c.get('provenance',{}).get('file_identity')or{}
  killed_prefix=bool(scope['fault_kind']=='death'and owned and old is not None and old['physical']
   and old['context']==context and len(rows)>=len(old_rows)>0 and rows[:len(old_rows)]==old_rows
   and all(type(old_file.get(k))is int and old_file[k]>0 and new_file.get(k)==old_file[k]for k in ('device','inode'))
   and summary.get('recording_closed')is False and summary.get('incomplete_final_row')is False
   and summary.get('discarded_tail_bytes')==0 and not any(r['event']=='END'for r in rows)
   and c.get('process_recheck')=={'pid':key[0],'start_ticks':key[1],'recording_end':None,
    'recording_closed':False,'result':'process_absent_without_exit_record'})
  bound=bool(live or postmortem or killed_prefix);physical[key]={'physical':bound,'context':context,'item':item,'closed':closed,'index':index,'killed_prefix':killed_prefix}
  ready=set();last=-1
  for seq,row in enumerate(rows):
   if(type(row)is not dict or row.get('seq')!=seq or type(row.get('monotonic_ns'))is not int
    or not 0<=row['monotonic_ns']<2**63 or row['monotonic_ns']<last):raise ValueError
   last=row['monotonic_ns'];payload=row.get('payload')or{};event=row.get('event')
   if event=='MODULE':
    from .vllm_lifecycle_profiles import source_hashes
    sources=source_hashes(sensor.PROFILE)
    if payload.get('verified')is True and payload.get('source_sha256')==sources.get(payload.get('module')):ready.add(payload['module'])
    else:ready.discard(payload.get('module'))
   if event=='FAULT_CONTROL':
    metadata.validate_control(payload)
    if payload['session']!=scope['session']or payload['target_sha256']!=scope['target_sha256']or payload['source_module']not in ready or payload['clock_context']!=_clock(context):raise ValueError
   elif event=='RANK_FAILURE':
    metadata.validate_rank(payload)
    if payload['source_verified']is True and payload['source_module']not in ready:raise ValueError
   elif event=='TERMINATION':
    from .vllm_termination_metadata import validate_sample
    validate_sample(payload)
   result.append({'index':index,'seq':seq,'time':row['monotonic_ns'],'event':event,'p':payload,
    'context':context,'physical':bound,'capture_sha256':c['provenance']['capture_sha256']})
 if len(result)>8192:raise ValueError
 coverage=bool(packet.get('recorded_files_covered')is True and packet.get('live_census_stable')is True
  and packet.get('request_index',{}).get('stored_record_index_complete')is True and not packet.get('python_processes_without_recording'))
 if active and not coverage:coverage=_active_inventory(packet,scope)
 return result,physical,coverage,owner


def validate_fault_case(packet,clientrow,injection_receipt,expected_scope,deadlines):
 result={**{key:False for key in FLAGS},'missing':[],'violations':[],'evidence_refs':[],
  'new_security_verdict':None,'worker_gpu_release_verified':False}
 try:return _project(packet,clientrow,injection_receipt,expected_scope,deadlines,result)
 except (ValueError,KeyError,TypeError,IndexError,AttributeError):
  result={**{key:False for key in FLAGS},'missing':['native24_scope_or_packet_invalid'],
   'violations':[],'evidence_refs':[],'new_security_verdict':None,'worker_gpu_release_verified':False}
  return result


def _project(packet,client,receipt,scope,deadlines,result):
 if(type(scope)is not dict or set(scope)!=SCOPE or scope['source_profile']!=sensor.PROFILE
  or not control.fingerprint(scope['session'],32)or not control.fingerprint(scope['nonce'],32)
  or not control.fingerprint(scope['target_sha256'])or not control.fingerprint(scope['external_request_sha256'])
  or type(scope['pod_uid'])is not str or not scope['pod_uid']or type(scope['container_id'])is not str or not scope['container_id']or type(scope['rank'])is not int or scope['rank']!=0
  or scope['fault_kind']not in control.KINDS or type(deadlines)is not dict
  or set(deadlines)!={'detection_ms','notification_ms','response_ms'}
  or any(type(v)is not int or not 0<v<=600000 for v in deadlines.values())):raise ValueError
 pre,pre_processes,pre_coverage,owner=_rows(receipt['pre_signal_packet'],scope,active=True)
 post,post_processes,post_coverage,post_owner=_rows(packet,scope,pre_processes)
 if owner!=post_owner:raise ValueError
 def exact(row):
  p=row['p'];return row['event']=='FAULT_CONTROL'and all(p[k]==scope[k]for k in ('session','target_sha256','nonce','fault_kind','rank','external_request_sha256'))
 assigned=[r for r in pre if exact(r)and r['p']['phase']=='ASSIGNED'and r['physical']]
 ready=[r for r in pre if exact(r)and r['p']['phase']=='READY'and r['physical']]
 if len(assigned)!=1 or len(ready)!=1:result['missing'].append('unique_physical_assignment_and_ready');return result
 assignment,active=assigned[0],ready[0];a=assignment['p'];b=active['p'];native=b['native_request_sha256']
 if a['native_request_sha256']!=native or assignment['time']>active['time']:raise ValueError
 # Source assignment must also agree with the original native8/23 ID assignment
 # in that same frontend process and live ASGI invocation.
 selected=[r for r in pre if r['physical']and r['index']==assignment['index']and r['event']=='SELECTION'
  and r['p'].get('point')=='InputProcessor.assign_request_id'and r['p'].get('phase')=='RETURN'
  and r['p'].get('metadata',{}).get('request_selection',{}).get('asgi_request_sha256')==scope['external_request_sha256']
  and r['p']['metadata']['request_selection'].get('selected_request_id_sha256')==native
  and r['p']['metadata']['request_selection'].get('asgi_invocation_sha256')==a['asgi_invocation_sha256']]
 if len(selected)!=1:result['missing'].append('exact_native_frontend_selection');return result
 # Include frontend termination and every source process used for coverage.
 # Locally consistent recording clocks cannot establish cross-process timing.
 clocks=[_clock(row['context'])for row in pre+post]+[client['clock_context'],receipt['clock_context']]
 for c in clocks:control.clock_context(c)
 result['clock_verified']=all(c==clocks[0]for c in clocks)
 if not result['clock_verified']:result['missing'].append('same_kernel_clock');return result
 # Reusing PID/start requires the full context to remain identical, including
 # independently live post recordings rather than only postmortem prefixes.
 if any(pre_processes[key]['context']!=post_processes[key]['context']for key in pre_processes.keys()&post_processes.keys()):
  result['missing'].append('same_process_generation_context');return result
 if(client.get('external_request_sha256')!=scope['external_request_sha256']or client.get('fault_nonce')!=scope['nonce']
  or receipt.get('nonce')!=scope['nonce']):raise ValueError
 for key in ('request_started_ns','response_finished_ns'):
  if type(client.get(key))is not int or not 0<client[key]<2**64:raise ValueError
 relevant=[r for r in post if exact(r)and r['p']['native_request_sha256']==native and r['physical']]
 phases={p:[r for r in relevant if r['p']['phase']==p]for p in metadata.PHASES}
 kind=scope['fault_kind'];begin=end=None
 if kind in {'death','delay'}:
  target=receipt.get('target')or{};physical=active['context']
  if(receipt.get('action')=='signal'and receipt.get('fault_kind')==kind and receipt.get('signal')==({'death':9,'delay':19}[kind])
   and target.get('pid')==physical['pid']and target.get('start_ticks')==physical['start_ticks']
   and receipt.get('ready_receipt_sha256')==b['ready_receipt_sha256']
   and receipt.get('observed_state')in ({'absent','Z','X'}if kind=='death'else{'T','t'})):
   begin=receipt.get('signal_before_ns');end=receipt.get('signal_after_ns')
   observed=receipt.get('state_observed_ns')
   if(type(observed)is not int or type(end)is not int or not end<=observed<b['barrier_deadline_ns']):begin=end=None
 else:
  activates=phases['ACTIVATE']
  if(len(activates)==1 and receipt.get('action')=='activate'and activates[0]['p']['action_receipt_sha256']==receipt.get('action_sha256')):
   begin=receipt.get('publish_before_ns');end=receipt.get('publish_after_ns')
   if kind=='exception':
    intent=phases['EXCEPTION_INTENT'];caught=phases['EXCEPTION_CAUGHT']
    if len(intent)!=1 or len(caught)!=1 or not begin<=intent[0]['time']<=caught[0]['time']:begin=end=None
   elif not b['async_output_bound']:begin=end=None
 if(type(begin)is not int or type(end)is not int or not active['time']<=begin<=end<b['barrier_deadline_ns']):
  result['missing'].append('source_bound_fault_occurrence');return result
 result['occurrence_verified']=True
 # Production Lab.requests preserves CLIENT's snake_case transport fields.
 # Retain historical packet aliases, but reject conflicting duplicate facts.
 def transport_fact(current,legacy):
  value=client.get(current,client.get(legacy))
  return value is True and (legacy not in client or client[legacy]is value)
 result['request_fault_overlap_verified']=bool(transport_fact('connection_established','connection')and transport_fact('request_sent','requestSent')
  and client['request_started_ns']<=begin<=end<=client['response_finished_ns'])
 if not result['request_fault_overlap_verified']:result['missing'].append('active_request_at_fault')
 # Native process coverage can use exact pre-signal physical identity after a
 # recorded PROCESS_EXIT. Import-only historical helpers never supply rank ACK.
 covered=True
 for process in post_processes.values():
  if process['physical']and(process['closed']or process['killed_prefix']):continue
  if retired_import_only(process['item'],assignment['context'],selected[0]['time'],owner):continue
  covered=False
 if not(pre_coverage and post_coverage and covered):result['missing'].append('closed_source_process_coverage')
 if any(r['event']=='TERMINATION'and r['p'].get('phase')=='OBSERVER_GAP'for r in post):result['missing'].append('observer_gap')
 rank_rows=[r for r in post if r['physical']and r['event']=='RANK_FAILURE'and r['p']['source_verified']is True
  and _clock(r['context'])==clocks[0]]
 paired=[]
 for r in rank_rows:
  if r['p']['phase']=='CALL':continue
  calls=[x for x in rank_rows if x['p']['phase']=='CALL'and x['context']==r['context']
   and all(x['p'][k]==r['p'][k]for k in ('point','call_sha256','owner_sha256','executor_sha256','native_request_sha256'))]
  if len(calls)==1 and calls[0]['time']<=r['p']['operation_started_ns']<=r['p']['operation_finished_ns']<=r['time']:paired.append(r)
 # Normal single-rank computation precedes ARM and binds the executor/worker
 # selection independently of the actuation receipt.
 executor_groups={r['p']['executor_sha256']for r in paired if r['p']['role']=='executor'and r['p']['world_size']==1 and r['p']['configured_output_rank']==0 and native in r['p']['native_request_sha256']and r['p']['executor_sha256']is not None}
 workers={(r['context']['pid'],r['context']['start_ticks'])for r in rank_rows if r['p']['role']=='worker'and r['p']['rank']==0 and r['p']['point']=='WorkerProc._execute_worker_rpc'and native in r['p']['native_request_sha256']}
 single=bool(len(executor_groups)==1 and workers=={(active['context']['pid'],active['context']['start_ticks'])})
 if not single:result['missing'].append('source_single_rank_executor')
 def stamp(row):return row.get('effect_ns')or row['p'].get('operation_finished_ns')or row['time']
 direct=[r for r in paired if native in r['p']['native_request_sha256']and r['p']['phase']=='RETURN'and r['p']['future_has_exception']is True
  and r['p']['point']=='FutureWrapper._wait_for_response'and r['p']['executor_sha256']in executor_groups and stamp(r)>=begin]
 if kind=='death':
  detected=[r for r in rank_rows if r['p']['monitor_caller_verified']and r['p']['monitor_dead_rank']==0
   and r['p']['monitor_dead_pid']==active['context']['pid']and r['p']['executor_sha256']in executor_groups and stamp(r)>=begin]
 elif kind=='exception':
  detected=[r for r in paired if r['context']['pid']==active['context']['pid']and r['context']['start_ticks']==active['context']['start_ticks']
   and native in r['p']['native_request_sha256']and r['p']['point']=='WorkerProc.handle_output'
   and r['p']['phase']=='RETURN'and r['p']['worker_output_is_exception']is True and stamp(r)>=begin]
  if len(phases['EXCEPTION_CAUGHT'])==1:detected=[dict(r,effect_ns=phases['EXCEPTION_CAUGHT'][0]['time'])for r in detected]
 else:detected=direct
 upper=[r for r in paired if r['p']['phase']=='RAISE'and r['p']['point']=='FutureWrapper.result'
  and native in r['p']['native_request_sha256']and r['p']['executor_sha256']in executor_groups and stamp(r)>=begin]
 if kind!='death':upper=[r for r in upper if any(x['p']['owner_sha256']==r['p']['owner_sha256']and x['p']['executor_sha256']==r['p']['executor_sha256']for x in direct)]
 if kind=='death':
  groups={r['p']['executor_sha256']for r in detected if r['p']['executor_sha256']is not None}
  upper +=[r for r in rank_rows if r['p']['executor_sha256']in groups and stamp(r)>=begin
   and(r['p']['executor_failed_marker_observed']or r['p']['point']=='EngineCoreProc._send_engine_dead'and r['p']['phase']=='RETURN')]
 if detected:upper=[r for r in upper if stamp(r)>=min(stamp(x)for x in detected)]
 killed=[v for v in post_processes.values()if v['killed_prefix']]
 result['expected_killed_prefixes']=[]
 if killed:
  monitor=[r for r in detected if r['p']['monitor_caller_verified']and r['p']['monitor_dead_pid']==active['context']['pid']
   and r['p']['monitor_exit_code_abs']==9 and r['p']['monitor_exit_code_sign']=='negative']
  for process in killed:
   ctx=process['context'];is_worker=(ctx['pid'],ctx['start_ticks'])==(active['context']['pid'],active['context']['start_ticks'])
   related=any(r['context']==ctx and(r['p']['executor_sha256']in executor_groups or r['p']['owner_sha256']in executor_groups)for r in rank_rows)
   is_frontend=ctx==assignment['context']
   if not(result['occurrence_verified']and monitor and(is_worker or related or is_frontend)):
    result['missing'].append('uncorroborated_absent_process_prefix');covered=False
   else:result['expected_killed_prefixes'].append({'recording':process['index'],'pid':ctx['pid'],'start_ticks':ctx['start_ticks'],'recording_closed':False,'scope':'pre_signal_live_identity_and_fatal_source_prefix'})
 def deadline(rows,key,flag,missing):
  if not rows:result['missing'].append(missing);return False
  r=min(rows,key=stamp);elapsed=stamp(r)-begin
  result[flag]=elapsed<=deadlines[key]*1_000_000
  if not result[flag]and stamp(r)-end>deadlines[key]*1_000_000:
   result['violations'].append({'code':missing+'_DEADLINE_EXCEEDED','elapsed_upper_ns':elapsed,'deadline_ms':deadlines[key]})
  elif not result[flag]:result['missing'].append(missing+'_clock_interval_crosses_deadline')
  result['evidence_refs'].append({'kind':'post','recording':r['index'],'seq':r['seq'],'capture_sha256':r['capture_sha256']})
  return result[flag]or bool(stamp(r)-end>deadlines[key]*1_000_000)
 detection_complete=deadline(detected,'detection_ms','detection_deadline_verified','native_detection')
 upper_complete=deadline(upper,'notification_ms','upper_notification_deadline_verified','upper_notification')
 terminal=[r for r in post if r['physical']and r['event']=='TERMINATION'and r['p'].get('point')=='ASGI.request'
  and r['p'].get('asgi_request_sha256')==scope['external_request_sha256']and r['p'].get('asgi_invocation_sha256')==a['asgi_invocation_sha256']
  and r['p'].get('phase')in {'ASGI_APP_RETURN','ASGI_APP_RAISE'}and r['time']>=begin]
 outcome=client.get('outcome');http_error=type(outcome)is int and 400<=outcome<=599 and client.get('complete')is True
 # A source exception does not establish an error at the client: a completed
 # successful HTTP response may already have reached it. Retain streamed
 # error responses only when the client actually observed incomplete transport.
 incomplete_response=(client.get('complete')is False and
  ((type(outcome)is int and 200<=outcome<=599)or(type(outcome)is str and outcome in {'error','timeout'})))
 source_error=incomplete_response and any(r['p']['phase']=='ASGI_APP_RAISE'for r in terminal)
 response_complete=bool(len(terminal)==1 and(http_error or source_error)and upper and client['response_finished_ns']>=min(stamp(r)for r in upper))
 if response_complete:
  elapsed=client['response_finished_ns']-begin
  result['affected_request_error_verified']=elapsed<=deadlines['response_ms']*1_000_000
  if not result['affected_request_error_verified']and client['response_finished_ns']-end>deadlines['response_ms']*1_000_000:
   result['violations'].append({'code':'affected_request_error_DEADLINE_EXCEEDED','deadline_ms':deadlines['response_ms']})
  elif not result['affected_request_error_verified']:result['missing'].append('affected_response_clock_interval_crosses_deadline')
 else:result['missing'].append('exact_affected_request_error_response')
 result['evidence_refs'][:0]=[{'kind':'pre','recording':r['index'],'seq':r['seq'],'capture_sha256':r['capture_sha256']}for r in (assignment,active)]
 result['whole_kind_complete']=bool(result['occurrence_verified']and result['request_fault_overlap_verified']and result['clock_verified']
  and pre_coverage and post_coverage and covered and single and detection_complete and upper_complete and response_complete and not result['missing'])
 return result
