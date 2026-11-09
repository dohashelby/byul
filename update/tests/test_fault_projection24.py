"""EXAMPLE typed/hashchained native24 packets; no GPU or OS signal."""
from copy import deepcopy
from pathlib import Path
import hashlib,json
import pytest
from guard.adapters import vllm_fault_sensor as sensor,vllm_fault_control as control,vllm_fault_metadata as metadata
from guard.adapters import vllm_lifecycle_sensor as base,vllm_termination_sensor as term,vllm_rank_failure_sensor as rank,vllm_rank_failure_metadata as rm
from guard.adapters.vllm_fault_projection import validate_fault_case
from guard.executors.lifecycle_capture import canonical_row,parse_recording
from guard.collectors.lifecycle import prepare_assets,load_prepared

SESSION='1'*32;NONCE='2'*32;TARGET='3'*64;EXT='4'*64;NATIVE='5'*64;INV='6'*64;GROUP='7'*64
CLOCK={'boot_sha256':'8'*64,'time_namespace':9,'time_offsets_sha256':'a'*64};B=1_000_000_000
OWNER={'pod_uid':'owned-pod','container_id':'containerd://'+'b'*64,'namespace_uid':'owned-namespace'}


def context(pid):return {'pid':pid,'start_ticks':100,'uid':2000,'pid_namespace':11,**CLOCK}

def control_row(kind,phase,pid=33):
 assignment=phase=='ASSIGNED';ipc=kind=='communication-stall'and not assignment
 return {'schema':sensor.EVENT_SCHEMA,'phase':phase,'point':'InputProcessor.assign_request_id'if assignment else'WorkerProc.enqueue_output'if ipc else'WorkerProc._execute_worker_rpc',
  'source_module':sensor.INPUT if assignment else sensor.MP,'source_sha256':metadata.control_source()[sensor.INPUT]if assignment else control.MP_SHA256,
  'body_code_sha256':'c'*64,'observer_runtime':'cpython-3.12.13','observer_code_sha256':sensor.RUNTIME_CODE_CONTRACTS['cpython-3.12.13'],
  'nonce':NONCE,'fault_kind':kind,'rank':0,'external_request_sha256':EXT,'native_request_sha256':NATIVE,'session':SESSION,'target_sha256':TARGET,
  'asgi_invocation_sha256':INV if assignment else None,'call_sha256':None if assignment else'd'*64,
  'ready_receipt_sha256':None if assignment else'f'*64,'action_receipt_sha256':'e'*64 if phase=='ACTIVATE'else None,
  'operation_started_ns':None if assignment else B+150,'observation_ns':B+({'ASSIGNED':110,'READY':200,'ACTIVATE':330,'EXCEPTION_INTENT':340,'EXCEPTION_CAUGHT':350,'RELEASE':600}[phase]),
  'barrier_deadline_ns':None if assignment else B+30_000_000_000,'clock_context':dict(CLOCK),
  'rpc_active':not assignment and not ipc,'runner_bound':not assignment,'async_output_bound':ipc,'worker_loop_observed':ipc}


def rank_row(point,phase,*,kind='exception',stamp=450,role=None):
 cls=point.split('.')[0];role=role or {'WorkerProc':'worker','MultiprocExecutor':'executor','FutureWrapper':'future','EngineCoreProc':'core'}[cls]
 v={k:None for k in rm.FIELDS};v.update(schema=rank.EVENT_SCHEMA,point=point,phase=phase,
  source_module=rank.MP if cls!='EngineCoreProc'else rank.CORE,source_sha256=rank.SOURCES[rank.MP if cls!='EngineCoreProc'else rank.CORE],source_verified=True,
  observer_runtime='cpython-3.12.13',observer_code_sha256=sensor.RANK_CODE_CONTRACTS['cpython-3.12.13'],
  owner_sha256='1'*64,executor_sha256=GROUP,call_sha256=hashlib.sha256((point+phase+str(stamp)).encode()).hexdigest(),
  parent_call_sha256=None,role=role,native_request_sha256=[NATIVE],rpc_method='execute_model',selected_reply_rank=0,non_block=True,
  fault_activation_verified=False,cross_process_group_verified=False,upper_client_receipt_verified=False,
  executor_failed_marker_observed=False,monitor_caller_verified=False)
 if role=='worker':v['rank']=0
 if role=='executor':v.update(world_size=1,configured_output_rank=0,executor_is_failed=False)
 if phase!='CALL':v.update(operation_started_ns=B+stamp-10,operation_finished_ns=B+stamp,
  returned_kind=('future'if role=='executor'and point=='MultiprocExecutor.execute_model'else'none')if phase=='RETURN'else None,exception_kind=('exception'if kind=='exception'else'timeout')if phase=='RAISE'else None)
 if role=='future':v.update(future_state='FINISHED',future_has_exception=phase=='RAISE')
 return v


def selection():
 value={k:None for k in (base.REQUEST_SELECTION_POINTS and {'asgi_request_sha256','asgi_invocation_sha256','entry_path_sha256','entry_root_path_sha256','entry_method_sha256','selected_session_sha256','selected_request_id_sha256','selected_priority','selected_rank'})}
 value.update(asgi_request_sha256=EXT,asgi_invocation_sha256=INV,entry_path_sha256=hashlib.sha256(b'/v1/chat/completions').hexdigest(),entry_root_path_sha256=hashlib.sha256(b'').hexdigest(),entry_method_sha256=hashlib.sha256(b'POST').hexdigest(),selected_request_id_sha256=NATIVE,
  priority_observed=False,session_observed=False,rank_observed=False,request_id_observed=True)
 return {'point':'InputProcessor.assign_request_id','phase':'RETURN','source_sha256':metadata.control_source()[sensor.INPUT],
  'requests':[],'metadata':{'request_selection':value},'selection_observed':False,'observation_started_ns':B+90,'observation_finished_ns':B+100}


def terminal():
 return dict(schema=term.EVENT_SCHEMA,point='ASGI.request',phase='ASGI_APP_RAISE',source_module='observer.asgi',source_sha256=sensor.ASSETS['guard_termination.py'],body_code_sha256='c'*64,
  observer_runtime='cpython-3.12.13',observer_code_sha256=term.RUNTIME_CODE_CONTRACTS['cpython-3.12.13'],call_sha256='d'*64,operation_started_ns=B+50,operation_finished_ns=B+800,
  request_sha256=[],gpu_work_selected_sha256=[],states=[],scheduler_before=[],scheduler_after=[],stream_kind=None,stream_sha256=None,stream_set_complete=False,caller_stream_bound=False,output_token=None,error_kind=None,
  asgi_request_sha256=EXT,asgi_invocation_sha256=INV,asgi_path_sha256=hashlib.sha256(b'/v1/chat/completions').hexdigest())


def recording(pid,events,sealed):
 settings={'schema':base.CONFIG_SCHEMA,'session':SESSION,'output_dir':'/guard-lifecycle/data/'+SESSION,'control_path':'/guard-lifecycle/data/'+SESSION+'/SEALED'}
 header={'schema':sensor.SCHEMA,'profile':sensor.PROFILE,'commit':sensor.COMMIT,'session':SESSION,
  'settings_sha256':hashlib.sha256((json.dumps(settings,sort_keys=True,indent=2)+'\n').encode()).hexdigest(),
  'sensor_sha256':hashlib.sha256(Path(sensor.__file__).read_bytes()).hexdigest(),'context':context(pid),'producer_authenticated':False}
 data=[('START',header,0),('MODULE',{'module':sensor.INPUT,'verified':True,'source_sha256':metadata.control_source()[sensor.INPUT],'code_sha256':'1'*64},1),
  ('MODULE',{'module':sensor.MP,'verified':True,'source_sha256':control.MP_SHA256,'code_sha256':'2'*64},2),
  ('MODULE',{'module':rank.CORE,'verified':True,'source_sha256':rank.SOURCES[rank.CORE],'code_sha256':'3'*64},3),*events]
 if sealed:data.append(('END',{'reason':'SEALED'},1000))
 expanded=[]
 for event,payload,stamp in data:
  if event=='RANK_FAILURE'and payload['phase']!='CALL':
   entry=deepcopy(payload);entry.update(phase='CALL',operation_started_ns=None,operation_finished_ns=None,returned_kind=None,exception_kind=None)
   if entry['role']=='future':entry.update(future_state='PENDING',future_has_exception=False)
   expanded.append((event,entry,payload['operation_started_ns']-B-1))
  expanded.append((event,payload,stamp))
 data=sorted(expanded,key=lambda row:row[2])
 previous='0'*64;rows=[]
 for event,payload,stamp in data:
  row={'seq':len(rows),'event':event,'monotonic_ns':B+stamp,'previous_sha256':previous,'payload':payload};previous=hashlib.sha256(canonical_row(row)).hexdigest();rows.append(row)
 raw=b''.join(canonical_row(row)for row in rows)
 prepared={'profile':sensor.PROFILE,'session':SESSION,'fault_control_target_sha256':TARGET,'assets':{'settings.json':{'sha256':header['settings_sha256']},'guard_lifecycle_sensor.py':{'sha256':header['sensor_sha256']}}}
 parsed,summary=parse_recording(raw,prepared)
 return {'pid':pid,'start_ticks':100,'session_owner_match':True,'listed_file_identity_match':True,
  'capture':{'records':parsed,'summary':summary,'process_context_verified':True,'image_digest_verified':True,'provenance':{'owner':dict(OWNER),'capture_sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),'file_identity':{'device':10,'inode':pid,'size':len(raw)}}}}


def case(kind='exception'):
 front=[('SELECTION',selection(),100),('FAULT_CONTROL',control_row(kind,'ASSIGNED',31),110)]
 worker=[('RANK_FAILURE',rank_row('WorkerProc._execute_worker_rpc','CALL',kind=kind,stamp=150),150),('FAULT_CONTROL',control_row(kind,'READY'),200)]
 parent=[('RANK_FAILURE',rank_row('MultiprocExecutor.execute_model','RETURN',kind=kind,stamp=130),130)]
 pre={'schema':'guard-lifecycle-session-capture/1','session':SESSION,'before':{'owner':dict(OWNER),'owner_stable':True},'after':{'owner':dict(OWNER),'owner_stable':True},
  'recorded_files_covered':True,'live_census_stable':True,'request_index':{'stored_record_index_complete':True},'python_processes_without_recording':[]}
 pre['recordings']=[recording(31,front,False),recording(32,parent,False),recording(33,worker,False)]
 if kind in {'exception','communication-stall'}:worker.append(('FAULT_CONTROL',control_row(kind,'ACTIVATE'),330))
 if kind=='exception':
  worker += [('FAULT_CONTROL',control_row(kind,p),s)for p,s in [('EXCEPTION_INTENT',340),('EXCEPTION_CAUGHT',350)]]
  detection=rank_row('WorkerProc.handle_output','RETURN',stamp=450);detection['worker_output_is_exception']=True;worker.append(('RANK_FAILURE',detection,450))
 elif kind=='death':
  detection=rank_row('MultiprocExecutor.shutdown','CALL',kind=kind,stamp=450);detection.update(monitor_caller_verified=True,monitor_dead_rank=0,monitor_dead_pid=33,monitor_exit_code_abs=9,monitor_exit_code_sign='negative');parent.append(('RANK_FAILURE',detection,450))
 else:
  detected=rank_row('FutureWrapper._wait_for_response','RETURN',kind=kind,stamp=450);detected['future_has_exception']=True;parent.append(('RANK_FAILURE',detected,450))
 if kind=='exception':
  ack=rank_row('FutureWrapper._wait_for_response','RETURN',kind=kind,stamp=470);ack['future_has_exception']=True;parent.append(('RANK_FAILURE',ack,470))
 parent.append(('RANK_FAILURE',rank_row('FutureWrapper.result','RAISE',kind=kind,stamp=500),500))
 front.append(('TERMINATION',terminal(),800))
 post={**deepcopy(pre),'recordings':[recording(31,front,True),recording(32,parent,True),recording(33,worker,True)]}
 client={'connection':True,'requestSent':True,'request_started_ns':B+50,'response_finished_ns':B+900,'clock_context':dict(CLOCK),'external_request_sha256':EXT,'fault_nonce':NONCE,'outcome':500,'complete':True}
 receipt={'pre_signal_packet':pre,'nonce':NONCE,'clock_context':dict(CLOCK)}
 if kind in {'death','delay'}:receipt.update(action='signal',fault_kind=kind,signal=9 if kind=='death'else 19,target={'pid':33,'start_ticks':100},signal_before_ns=B+300,signal_after_ns=B+320,state_observed_ns=B+325,observed_state='absent'if kind=='death'else'T',ready_receipt_sha256='f'*64)
 else:receipt.update(action='activate',action_sha256='e'*64,publish_before_ns=B+300,publish_after_ns=B+320)
 scope={'source_profile':sensor.PROFILE,'session':SESSION,'target_sha256':TARGET,'pod_uid':OWNER['pod_uid'],'container_id':OWNER['container_id'],'external_request_sha256':EXT,'nonce':NONCE,'fault_kind':kind,'rank':0}
 return post,client,receipt,scope,{'detection_ms':1,'notification_ms':1,'response_ms':1}


@pytest.mark.parametrize('kind',sorted(control.KINDS))
def test_each_exact_source_fault_and_native_ack_joins_same_request_clock(kind):
 args=case(kind);r=validate_fault_case(*args)
 assert all(r[k]for k in ('occurrence_verified','request_fault_overlap_verified','detection_deadline_verified','upper_notification_deadline_verified','affected_request_error_verified','clock_verified','whole_kind_complete')),r
 assert not r['missing']and not r['violations']and r['evidence_refs']and r['new_security_verdict']is None and not r['worker_gpu_release_verified']


@pytest.mark.parametrize('kind',sorted(control.KINDS))
@pytest.mark.parametrize('status',[200,201,204,302])
def test_completed_non_error_client_response_cannot_be_proven_failed_by_source_raise(kind,status):
 args=case(kind);args[1].update(outcome=status,complete=True)
 result=validate_fault_case(*args)
 assert result['affected_request_error_verified']is False
 assert result['whole_kind_complete']is False
 assert 'exact_affected_request_error_response'in result['missing']


@pytest.mark.parametrize('kind',sorted(control.KINDS))
@pytest.mark.parametrize('complete',[None,0,'false'])
def test_missing_or_untyped_transport_completion_is_not_a_client_error_receipt(kind,complete):
 args=case(kind);args[1].update(outcome=200,complete=complete)
 result=validate_fault_case(*args)
 assert result['affected_request_error_verified']is False
 assert result['whole_kind_complete']is False


@pytest.mark.parametrize('kind',sorted(control.KINDS))
@pytest.mark.parametrize('outcome',[200,500,'error','timeout'])
def test_incomplete_client_transport_with_exact_source_raise_preserves_error_evidence(kind,outcome):
 args=case(kind);args[1].update(outcome=outcome,complete=False)
 result=validate_fault_case(*args)
 assert result['affected_request_error_verified']is True
 assert result['whole_kind_complete']is True


def test_prepare24_requires_exact_target_and_exports_nine_fixed_assets(tmp_path):
 out=tmp_path/'assets';m=prepare_assets(out,Path(__file__).resolve().parents[2],profile=sensor.PROFILE,fault_control_target_sha256=TARGET)
 assert len(m['assets'])==9 and m['fault_control_target_sha256']==TARGET and load_prepared(out/'manifest.json')==m
 with pytest.raises(ValueError):prepare_assets(tmp_path/'missing',tmp_path,profile=sensor.PROFILE)
 with pytest.raises(ValueError):prepare_assets(tmp_path/'legacy',tmp_path,profile=base.PROFILE,fault_control_target_sha256=TARGET)


def rehash(recording):
 c=recording['capture'];previous='0'*64
 for seq,row in enumerate(c['records']):
  row['seq']=seq;row['previous_sha256']=previous;previous=hashlib.sha256(canonical_row(row)).hexdigest()
 raw=b''.join(canonical_row(row)for row in c['records']);h=c['records'][0]['payload']
 prepared={'profile':sensor.PROFILE,'session':SESSION,'fault_control_target_sha256':TARGET,'assets':{'settings.json':{'sha256':h['settings_sha256']},'guard_lifecycle_sensor.py':{'sha256':h['sensor_sha256']}}}
 _,c['summary']=parse_recording(raw,prepared);c['provenance'].update(capture_sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw))


@pytest.mark.parametrize('damage',['nonce','target','clock','client-finished','ready-sha','image','owner','tail','no-catch','no-upper','no-worker','world-two','action-sha','wrong-native','open-post','duplicate-selection','source-tamper','state-clock'])
def test_partial_or_changed_physical_source_cannot_become_whole_fault(damage):
 kind='death'if damage in {'ready-sha','state-clock'}else'exception'
 p,c,i,s,d=case(kind)
 if damage=='nonce':i['nonce']='0'*32
 if damage=='target':s['container_id']='containerd://'+'0'*64
 if damage=='clock':c['clock_context']['time_namespace']=99
 if damage=='client-finished':c['response_finished_ns']=B+299
 if damage=='ready-sha':i['ready_receipt_sha256']='0'*64
 if damage=='action-sha':i['action_sha256']='0'*64
 if damage=='state-clock':i['state_observed_ns']=B+299
 if damage=='image':p['recordings'][2]['capture']['image_digest_verified']=False
 if damage=='owner':p['after']['owner']['pod_uid']='other'
 if damage=='tail':p['recordings'][0]['capture']['summary']['discarded_tail_bytes']=1
 if damage in {'no-catch','no-upper','no-worker','world-two','wrong-native','open-post','duplicate-selection'}:
  for r in p['recordings']:
   rows=r['capture']['records']
   if damage=='no-catch':rows[:]=[x for x in rows if x['payload'].get('phase')!='EXCEPTION_CAUGHT']
   if damage=='no-upper':rows[:]=[x for x in rows if x['payload'].get('point')!='FutureWrapper.result']
   if damage=='no-worker':rows[:]=[x for x in rows if x['payload'].get('point')!='WorkerProc._execute_worker_rpc'or x['event']=='FAULT_CONTROL']
   if damage=='world-two':
    for x in rows:
     if x['payload'].get('role')=='executor':x['payload']['world_size']=2
   if damage=='wrong-native':
    for x in rows:
     if x['payload'].get('point')=='FutureWrapper.result':x['payload']['native_request_sha256']=['0'*64]
   if damage=='open-post':rows[:]=[x for x in rows if x['event']!='END']
   rehash(r)
  if damage=='no-worker':
   for r in i['pre_signal_packet']['recordings']:
    r['capture']['records'][:]=[x for x in r['capture']['records']if x['payload'].get('point')!='WorkerProc._execute_worker_rpc'or x['event']=='FAULT_CONTROL'];rehash(r)
  if damage=='duplicate-selection':
   r=i['pre_signal_packet']['recordings'][0];r['capture']['records'].insert(-1,deepcopy(r['capture']['records'][-2]));rehash(r)
 if damage=='source-tamper':p['recordings'][1]['capture']['records'][4]['payload']['private_debug']='EXAMPLE_PRIVATE'
 result=validate_fault_case(p,c,i,s,d)
 assert not result['whole_kind_complete']and result['missing']and result['new_security_verdict']is None,result


def test_confirmed_late_native_ack_keeps_violation_and_evidence_complete_scope():
 p,c,i,s,d=case('delay')
 for r in p['recordings']:
  for row in r['capture']['records']:
   if row['event']=='RANK_FAILURE'and row['payload']['point']=='FutureWrapper._wait_for_response'and row['payload']['phase']!='CALL':
    row['monotonic_ns']=B+2_000_000;row['payload']['operation_started_ns']=B+1_900_000;row['payload']['operation_finished_ns']=B+2_000_000
   if row['event']=='RANK_FAILURE'and row['payload']['point']=='FutureWrapper.result'and row['payload']['phase']!='CALL':
    row['monotonic_ns']=B+2_100_000;row['payload']['operation_started_ns']=B+1_900_000;row['payload']['operation_finished_ns']=B+2_100_000
   if row['event']=='TERMINATION':row['monotonic_ns']=B+2_500_000;row['payload']['operation_finished_ns']=B+2_500_000
   if row['event']=='END':row['monotonic_ns']=B+3_000_000
  r['capture']['records'].sort(key=lambda row:row['monotonic_ns']);rehash(r)
 c['response_finished_ns']=B+2_900_000
 result=validate_fault_case(p,c,i,s,d)
 assert result['whole_kind_complete']and not result['detection_deadline_verified']and result['violations'],result


def test_sigkill_prefix_has_physical_corroboration_without_inventing_end_or_resource_release():
 p,c,i,s,d=case('death');item=p['recordings'][2];capture=item['capture']
 capture['records'][:]=[row for row in capture['records']if row['event']!='END'];rehash(item)
 capture['process_context_verified']=False
 capture['process_recheck']={'pid':33,'start_ticks':100,'recording_end':None,'recording_closed':False,'result':'process_absent_without_exit_record'}
 r=validate_fault_case(p,c,i,s,d)
 assert r['whole_kind_complete']and r['expected_killed_prefixes']and not r['worker_gpu_release_verified'],r
 assert not capture['summary']['recording_closed']and not any(row['event']=='END'for row in capture['records'])
 # Loss of the original physical file identity never becomes a SIGKILL proof.
 capture['provenance']['file_identity']['inode']=99
 r=validate_fault_case(p,c,i,s,d);assert not r['whole_kind_complete']and r['missing']


def active_prefix_case(kind='exception'):
 p,c,i,s,d=case(kind);pre=i['pre_signal_packet'];files=[];processes=[]
 for item in pre['recordings']:
  captured=item['capture'];ctx=captured['records'][0]['payload']['context'];size=captured['provenance']['bytes']
  captured['process_context_verified']=False;item['listed_file_identity_match']=False
  captured['active_prefix_binding']={'schema':'guard-live-lifecycle-prefix/1','context_sha256':hashlib.sha256(json.dumps(ctx,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
   'capture_sha256':captured['provenance']['capture_sha256'],'bytes':size,'later_capture_sha256':'a'*64,'later_bytes':size+1,
   'file_device':10,'file_inode':item['pid'],'process_context_identity_verified':True,'prefix_extension_verified':True}
  files.append({'pid':item['pid'],'start_ticks':100,'bytes':size-1,'uid':2000,'mode':0o600,'device':10,'inode':item['pid'],'mtime_ns':1,'ctime_ns':1,'regular':True})
  processes.append({**ctx,'parent_pid':1,'same_executable':True})
 directory={'device':10,'inode':99,'uid':2000,'mode':0o700,'mtime_ns':1}
 m={'schema':'guard-lifecycle-session-inventory/1','session':SESSION,'directory_before':directory,'directory_after':dict(directory),'directory_stable':True,'sealed':False,
  'files':files,'unknown_entries':0,'files_truncated':False,'processes':processes,'unreadable_pids':[],'processes_truncated':False}
 pre['before']['metadata']=m;pre['after']['metadata']=deepcopy(m)
 for row in pre['after']['metadata']['files']:row['bytes']+=2;row['mtime_ns']+=1;row['ctime_ns']+=1
 pre['live_census_stable']=False;pre['recorded_files_covered']=False
 return p,c,i,s,d


def test_live_append_prefix_keeps_global_flags_false_but_exact_pid_and_file_scope_usable():
 p,c,i,s,d=active_prefix_case();pre=i['pre_signal_packet']
 result=validate_fault_case(p,c,i,s,d)
 assert result['whole_kind_complete'],result
 assert not pre['recorded_files_covered']and not pre['live_census_stable']
 # Any PID generation or physical file replacement still closes this prefix.
 pre['after']['metadata']['files'][0]['inode']=88
 assert not validate_fault_case(p,c,i,s,d)['whole_kind_complete']


def test_actual_capture_append_receipt_never_claims_global_closed_process_binding(monkeypatch):
 from types import SimpleNamespace
 from guard.executors import lifecycle_capture as mod
 item=recording(31,[('SELECTION',selection(),100),('FAULT_CONTROL',control_row('exception','ASSIGNED',31),110)],False)
 rows=item['capture']['records'];raw=b''.join(canonical_row(x)for x in rows)
 more={'seq':len(rows),'event':'MODULE','monotonic_ns':B+120,'previous_sha256':hashlib.sha256(canonical_row(rows[-1])).hexdigest(),
  'payload':{'module':sensor.MP,'verified':True,'source_sha256':control.MP_SHA256,'code_sha256':'2'*64}}
 later=raw+canonical_row(more);ctx=rows[0]['payload']['context'];calls=[]
 def read(*a,**kw):
  data=raw if not calls else later;calls.append(1)
  return data,{'owner':{**OWNER,'started_at':'2026-11-07T00:00:00Z'},'owner_binding_verified':True,
   'capture_sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data),'file_identity':{'device':10,'inode':31,'size':len(data),'mtime_ns':len(data),'ctime_ns':len(data)}}
 monkeypatch.setattr(mod,'process_context',lambda *a:dict(ctx))
 prepared={'profile':sensor.PROFILE,'session':SESSION,'fault_control_target_sha256':TARGET,'assets':{'settings.json':{'sha256':rows[0]['payload']['settings_sha256']},'guard_lifecycle_sensor.py':{'sha256':rows[0]['payload']['sensor_sha256']}}}
 value=mod.capture(None,{},source={'container':'owned-engine','image':'example.invalid/engine@sha256:'+'a'*64,'path':'/guard-lifecycle/data/'+SESSION+'/pid-31-100.jsonl'},prepared=prepared,read_file=read,image_check=lambda *a:True)
 assert value['process_context_verified']is False and not value['summary']['recording_closed']
 assert value['active_prefix_binding']['prefix_extension_verified']and value['active_prefix_binding']['process_context_identity_verified']
 assert value['records']==rows and value['producer_authenticated']is False and value['security']=='UNKNOWN'


@pytest.mark.parametrize('kind',sorted(control.KINDS))
@pytest.mark.parametrize('field,value',[('boot_sha256','0'*64),('time_namespace',999),('time_offsets_sha256','0'*64),('uid',2001),('pid_namespace',999)])
def test_post_source_clock_and_generation_context_fail_closed(kind,field,value):
 args=case(kind);front=args[0]['recordings'][0]
 front['capture']['records'][0]['payload']['context'][field]=value
 for row in front['capture']['records']:
  if row['event']=='FAULT_CONTROL'and field in control.CLOCK_FIELDS:row['payload']['clock_context'][field]=value
 rehash(front)
 result=validate_fault_case(*args)
 assert not result['whole_kind_complete']
 assert not result['affected_request_error_verified']
 assert result['missing']
 if field in control.CLOCK_FIELDS:assert not result['clock_verified']


@pytest.mark.parametrize('kind',sorted(control.KINDS))
@pytest.mark.parametrize('reported_missing',[True,False])
def test_active_inventory_cannot_restore_coverage_for_unrecorded_python(kind,reported_missing):
 p,c,i,s,d=active_prefix_case(kind);pre=i['pre_signal_packet']
 extra={**pre['before']['metadata']['processes'][0],'pid':77,'start_ticks':200}
 for snapshot in (pre['before'],pre['after']):snapshot['metadata']['processes'].append(deepcopy(extra))
 pre['python_processes_without_recording']=[{'pid':77,'start_ticks':200}]if reported_missing else[]
 assert p['python_processes_without_recording']==[]
 result=validate_fault_case(p,c,i,s,d)
 assert not result['whole_kind_complete']
 assert 'closed_source_process_coverage'in result['missing']
