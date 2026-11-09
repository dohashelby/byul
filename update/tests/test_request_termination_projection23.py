"""EXAMPLE parsed packets test closure logic, not actual GPU execution."""
from copy import deepcopy
from hashlib import sha256
import pytest
from guard.adapters import vllm_termination_sensor as sensor
from guard.adapters.vllm_termination_projection import project_native
from guard.adapters.vllm_request_termination import SCOPES

EXT='a'*64;NATIVE='b'*64;INV='c'*64;BASE=1_000_000_000

def packet():
 context={'pid':42,'start_ticks':100,'boot_sha256':'d'*64,'pid_namespace':9,'time_namespace':10,'time_offsets_sha256':'e'*64}
 rows=[]
 def add(event,payload,stamp):rows.append({'event':event,'payload':payload,'seq':len(rows),'monotonic_ns':BASE+stamp})
 def term(point,phase,call='f'*64,**extra):
  module=next((name for name,classes in sensor.TARGETS.items()for cls,methods in classes.items()if point in {cls+'.'+m for m in methods}),'observer.asgi')
  return dict(schema=sensor.EVENT_SCHEMA,point=point,phase=phase,source_module=module,source_sha256=sensor.SOURCE_ADDITIONS.get(module,'1'*64),body_code_sha256='2'*64,observer_runtime='cpython-3.12.13',observer_code_sha256=sensor.RUNTIME_CODE_CONTRACTS['cpython-3.12.13'],call_sha256=call,operation_started_ns=BASE,operation_finished_ns=None if phase=='CALL'else BASE+1,request_sha256=[]if point=='ASGI.request'else[NATIVE],gpu_work_selected_sha256=[NATIVE]if point=='GPUModelRunner.execute_model'else[],states=[],scheduler_before=[],scheduler_after=[],stream_kind=None,stream_sha256=None,stream_set_complete=False,caller_stream_bound=False,output_token=None,error_kind=None,asgi_request_sha256=EXT if point=='ASGI.request'else None,asgi_invocation_sha256=INV if point=='ASGI.request'else None,asgi_path_sha256=sha256(b'/v1/chat/completions').hexdigest()if point=='ASGI.request'else None,**extra)
 def native(point,state,**meta):return {'point':point,'phase':'RETURN','requests':[{'request_sha256':NATIVE,**state}],'metadata':meta}
 add('START',{'profile':sensor.PROFILE,'context':context,'session':'a'*32,'sensor_sha256':'8'*64,'settings_sha256':'9'*64},0)
 add('NATIVE',native('InputProcessor.assign_request_id',{},external_request_sha256=EXT,asgi_request_sha256=EXT),1)
 selection=dict(asgi_request_sha256=EXT,asgi_invocation_sha256=INV,selected_request_id_sha256=NATIVE,request_id_observed=True,entry_path_sha256=sha256(b'/v1/chat/completions').hexdigest(),entry_method_sha256=sha256(b'POST').hexdigest())
 add('SELECTION',dict(point='InputProcessor.assign_request_id',phase='RETURN',metadata={'request_selection':selection}),2)
 add('TERMINATION',term('GPUModelRunner.execute_model','CALL'),3)
 add('TERMINATION',term('GPUModelRunner.execute_model','RETURN'),4)
 for phase,stamp in [('FENCE_RECORDED',5),('FENCE_COMPLETE',16)]:
  for kind in ('main','copy'):
   t=term('GPUModelRunner.execute_model',phase);t.update(stream_kind=kind,stream_sha256=('3'if kind=='main'else'4')*64,stream_set_complete=True,caller_stream_bound=True);add('TERMINATION',t,stamp)
 for point,phase,stamp in [('AsyncOutput.__init__','OUTPUT_CREATED',6),('AsyncOutput.get_output','CALL',7),('AsyncOutput.get_output','RETURN',17),('AsyncOutput.__init__','OUTPUT_RELEASED',18)]:
  t=term(point,phase);t['output_token']='5'*64;add('TERMINATION',t,stamp)
 add('TERMINATION',term('ASGI.request','ASGI_DISCONNECT_RECEIVED'),10)
 add('NATIVE',native('OutputProcessor.abort_requests',{'output_state_present':False}),11)
 add('NATIVE',native('EngineCore.abort_requests',{'request_present':False}),12)
 add('NATIVE',native('KVCacheManager.free',{},ownership={'complete':True,'groups':[{'request_sha256':NATIVE,'block_sha256':[],'pool_sha256':'7'*64}]}),13)
 add('NATIVE',native('Scheduler.finish_requests',{'request_present':False},deferred_complete=True,deferred_fences=[],ownership={'complete':True,'groups':[{'request_sha256':NATIVE,'block_sha256':[],'pool_sha256':'7'*64}]}),14)
 t=term('GPUModelRunner.finish_requests','RETURN',call='6'*64);t['states']=[dict(request_sha256=NATIVE,worker_slot_present=False,worker_mapping_complete=True)];add('TERMINATION',t,19)
 add('TERMINATION',term('ASGI.request','ASGI_APP_RETURN'),20)
 rows.sort(key=lambda r:r['monotonic_ns'])
 for seq,row in enumerate(rows):row['seq']=seq
 add('END',{'reason':'SEALED'},21)
 capture={'records':rows,'summary':{'recording_closed':True,'recording_end':'SEALED'},'process_context_verified':True,'image_digest_verified':True,'provenance':{'owner':{'id':'selected-container'}}}
 return {'session':'a'*32,'before':{'owner':{'id':'selected-container'},'owner_stable':True,'metadata':{'sealed':True}},'after':{'owner':{'id':'selected-container'},'owner_stable':True,'metadata':{'sealed':True}},'recordings':[{'capture':capture,'session_owner_match':True,'listed_file_identity_match':True}],'recorded_files_covered':True,'live_census_stable':True,'request_index':{'stored_record_index_complete':True},'python_processes_without_recording':[]},term


def result(p,required=None):return project_native(p,EXT,1,list(SCOPES)if required is None else required)

def test_example_full_positive_receipts_and_both_route_binding():
 p,_=packet();r=result(p)
 assert r['binding_observed']and r['whole_scope_complete']and r['activation_scope_complete']and r['worker_gpu_release_verified']
 assert not r['activation_violations']and not r['activation_missing']
 assert all(s['termination_verified']for s in r['scope_status'])


@pytest.mark.parametrize('damage',['missing-fence','pending-fence','wrong-stream','unqualified-stream','missing-output-release','missing-worker','missing-kv','deferred-kv','wrong-clock','image-unverified','bound','census','index','unrecorded-python','duplicate-invocation','gap','malformed','changed-session','changed-owner','unsealed','wrong-pool','fence-before-return','output-without-call'])
def test_each_missing_boundary_stays_unknown(damage):
 p,term=packet();c=p['recordings'][0]['capture'];rows=c['records']
 if damage=='missing-fence':rows[:]=[r for r in rows if not(r['event']=='TERMINATION'and r['payload']['phase']=='FENCE_COMPLETE')]
 elif damage=='pending-fence':
  for r in rows:
   if r['event']=='TERMINATION'and r['payload']['phase']=='FENCE_COMPLETE':r['payload']['phase']='FENCE_PENDING'
 elif damage=='wrong-stream':
  for r in rows:
   if r['event']=='TERMINATION'and r['payload']['phase']=='FENCE_COMPLETE':r['payload']['stream_sha256']='0'*64
 elif damage=='unqualified-stream':
  for r in rows:
   if r['event']=='TERMINATION':r['payload']['stream_set_complete']=False
 elif damage=='missing-output-release':rows[:]=[r for r in rows if r['payload'].get('phase')!='OUTPUT_RELEASED']
 elif damage=='missing-worker':rows[:]=[r for r in rows if r['payload'].get('point')!='GPUModelRunner.finish_requests']
 elif damage=='missing-kv':rows[:]=[r for r in rows if r['payload'].get('point')!='KVCacheManager.free']
 elif damage=='deferred-kv':
  for r in rows:
   if r['payload'].get('point')=='Scheduler.finish_requests':r['payload']['metadata']['deferred_fences']=[7]
 elif damage=='wrong-clock':
  other=deepcopy(p['recordings'][0]);other['capture']['records']=[deepcopy(rows[0]),*[r for r in rows if r['payload'].get('point','').startswith('GPUModelRunner.')],deepcopy(rows[-1])];other['capture']['records'][0]['payload']['context']['time_namespace']=11;p['recordings'].append(other);rows[:]=[r for r in rows if not r['payload'].get('point','').startswith('GPUModelRunner.')]
 elif damage=='image-unverified':c['image_digest_verified']=False
 elif damage=='bound':c['summary']['recording_end']='BOUND_REACHED';rows[-1]['payload']['reason']='BOUND_REACHED'
 elif damage=='census':p['live_census_stable']=False
 elif damage=='index':p['request_index']['stored_record_index_complete']=False
 elif damage=='unrecorded-python':p['python_processes_without_recording']=[{'pid':99}]
 elif damage=='duplicate-invocation':p['recordings'].append(deepcopy(p['recordings'][0]))
 elif damage=='gap':rows.insert(-1,dict(event='TERMINATION',payload=term('GPUModelRunner.execute_model','OBSERVER_GAP'),seq=100,monotonic_ns=BASE+20))
 elif damage=='malformed':rows[4]['payload']['private']='EXAMPLE_PRIVATE'
 elif damage=='changed-session':rows[0]['payload']['session']='b'*32
 elif damage=='changed-owner':c['provenance']['owner']={'id':'other-container'}
 elif damage=='unsealed':p['before']['metadata']['sealed']=False
 elif damage=='wrong-pool':
  for r in rows:
   if r['payload'].get('point')=='Scheduler.finish_requests':r['payload']['metadata']['ownership']['groups'][0]['pool_sha256']='0'*64
 elif damage=='fence-before-return':
  for r in rows:
   if r['payload'].get('phase')=='FENCE_COMPLETE':r['monotonic_ns']=BASE
 elif damage=='output-without-call':rows[:]=[r for r in rows if not(r['payload'].get('point')=='AsyncOutput.get_output'and r['payload']['phase']=='CALL')]
 r=result(p);assert not r['whole_scope_complete']and not r['activation_violations']


def test_retained_slot_after_deadline_is_positive_violation_only_with_full_coverage():
 p,term=packet();rows=p['recordings'][0]['capture']['records'];t=term('GPUModelRunner.finish_requests','STATE_SNAPSHOT',call='7'*64);t['states']=[dict(request_sha256=NATIVE,worker_slot_present=True,worker_mapping_complete=True)];t['operation_started_ns']=t['operation_finished_ns']=BASE+2_000_000;rows.insert(-1,dict(event='TERMINATION',payload=t,seq=90,monotonic_ns=BASE+2_000_000))
 r=result(p);assert not r['whole_scope_complete']and r['activation_scope_complete']and not r['activation_missing']and len(r['activation_violations'])==1 and r['activation_violations'][0]['scope']=='worker_batch'
 p['recorded_files_covered']=False;r=result(p);assert not r['activation_violations']and r['deadline_conflict_candidates'][0]['confirmed_violation']is False


def test_gpu_pending_after_deadline_preserves_residual_and_optional_scope_boundary():
 p,_=packet()
 for r in p['recordings'][0]['capture']['records']:
  if r['payload'].get('phase')=='FENCE_COMPLETE':r['payload']['phase']='FENCE_PENDING';r['monotonic_ns']=BASE+2_000_000
 r=result(p);assert len(r['deadline_conflict_candidates'])==2 and not r['activation_violations']and all(v['confirmed_violation']is False for v in r['deadline_conflict_candidates'])
 r=result(p,['asgi_transport','frontend_request']);assert r['requested_scope_complete']and not r['whole_scope_complete']and not r['activation_scope_complete']and not r['worker_gpu_release_verified']and not r['activation_violations']


def test_write_time_after_deadline_never_substitutes_for_state_read_time():
 p,term=packet();rows=p['recordings'][0]['capture']['records'];t=term('GPUModelRunner.finish_requests','STATE_SNAPSHOT',call='7'*64);t['states']=[dict(request_sha256=NATIVE,worker_slot_present=True,worker_mapping_complete=True)]
 rows.insert(-1,dict(event='TERMINATION',payload=t,seq=90,monotonic_ns=BASE+2_000_000))
 r=result(p);assert not r['activation_violations']and r['deadline_conflict_candidates'][0]['confirmed_violation']is False


@pytest.mark.parametrize('damage',['kv','scheduler'])
def test_resource_release_flag_keeps_its_complete_scope_under_small_required_subset(damage):
 p,_=packet();rows=p['recordings'][0]['capture']['records']
 if damage=='kv':rows[:]=[r for r in rows if r['payload'].get('point')!='KVCacheManager.free']
 else:
  for r in rows:
   if r['payload'].get('point')=='Scheduler.finish_requests':r['payload']['requests'][0]['request_present']=True
 r=result(p,['asgi_transport','frontend_request']);assert r['requested_scope_complete']and not r['whole_scope_complete']and not r['activation_scope_complete']and not r['worker_gpu_release_verified']


def with_retired_import_process():
 from guard.adapters.vllm_lifecycle_profiles import source_hashes
 p,_=packet();p['recordings'][0]['capture']['records'][0]['payload']['context']['uid']=1000
 helper=deepcopy(p['recordings'][0]);capture=helper['capture'];header=deepcopy(capture['records'][0]);header['payload']['context'].update(pid=73,start_ticks=90)
 name=next(iter(source_hashes(sensor.PROFILE)));header['monotonic_ns']=BASE-100
 capture['records']=[header,{'seq':1,'event':'MODULE','monotonic_ns':BASE-90,'payload':{'module':name,'verified':True,'source_sha256':source_hashes(sensor.PROFILE)[name],'code_sha256':'1'*64}},
  {'seq':2,'event':'END','monotonic_ns':BASE-80,'payload':{'reason':'PROCESS_EXIT'}}]
 capture['summary']={'recording_closed':True,'recording_end':'PROCESS_EXIT','incomplete_final_row':False,'discarded_tail_bytes':0}
 capture['process_context_verified']=False;capture['provenance']['capture_sha256']='2'*64
 capture['process_recheck']={'pid':73,'start_ticks':90,'recording_closed':True,'recording_end':'PROCESS_EXIT','result':'process_exited'}
 p['recordings'].append(helper);return p,capture


def test_closed_import_only_process_before_assignment_keeps_original_and_supplies_no_worker_proof():
 p,capture=with_retired_import_process();original=deepcopy(capture);r=result(p)
 assert r['whole_scope_complete']and r['activation_scope_complete']
 assert r['historical_import_only_recordings']==[{'recording':1,'pid':73,'start_ticks':90,'capture_sha256':'2'*64,'reason':'closed_import_only_before_native_assignment'}]
 assert p['recordings'][1]['capture']==original and not capture['process_context_verified']
 # Removing the actual worker request-state receipt still closes full scope.
 p['recordings'][0]['capture']['records'][:]=[x for x in p['recordings'][0]['capture']['records']if x['payload'].get('point')!='GPUModelRunner.finish_requests']
 assert not result(p)['whole_scope_complete']


@pytest.mark.parametrize('damage',['native-event','late-end','unclosed','wrong-owner','wrong-image','wrong-clock','wrong-recheck','unverified-module','unknown-source','tail','missing-uid'])
def test_retired_process_cannot_hide_work_or_incomplete_physical_scope(damage):
 p,c=with_retired_import_process()
 if damage=='native-event':c['records'].insert(-1,{'event':'NATIVE','seq':2,'monotonic_ns':BASE-85,'payload':{'point':'Scheduler.finish_requests','phase':'RETURN','requests':[],'metadata':{}}})
 if damage=='late-end':c['records'][-1]['monotonic_ns']=BASE+2
 if damage=='unclosed':c['summary']['recording_closed']=False
 if damage=='wrong-owner':c['provenance']['owner']={'id':'other'}
 if damage=='wrong-image':c['image_digest_verified']=False
 if damage=='wrong-clock':c['records'][0]['payload']['context']['time_namespace']=11
 if damage=='wrong-recheck':c['process_recheck']['start_ticks']=91
 if damage=='unverified-module':c['records'][1]['payload']['verified']=False
 if damage=='unknown-source':c['records'][1]['payload']['source_sha256']='3'*64
 if damage=='tail':c['summary']['discarded_tail_bytes']=1
 if damage=='missing-uid':c['records'][0]['payload']['context'].pop('uid')
 r=result(p);assert not r['whole_scope_complete']and not r['activation_scope_complete']and not r['historical_import_only_recordings']


def append_worker_sample(p,term,stamp,active):
 rows=p['recordings'][0]['capture']['records'];t=term('GPUModelRunner.finish_requests','STATE_SNAPSHOT',call='7'*64)
 t['states']=[dict(request_sha256=NATIVE,worker_slot_present=active,worker_mapping_complete=True)]
 t['operation_started_ns']=t['operation_finished_ns']=BASE+stamp
 rows.insert(-1,dict(event='TERMINATION',payload=t,seq=len(rows),monotonic_ns=BASE+stamp))
 rows[-1]['monotonic_ns']=max(r['monotonic_ns']for r in rows[:-1])+1
 for seq,row in enumerate(rows):row['seq']=seq


def test_repeated_empty_worker_samples_after_deadline_preserve_first_completion():
 p,term=packet();before=result(p)
 for stamp in (100,900_000,1_000_011,2_000_000):append_worker_sample(p,term,stamp,False)
 after=result(p)
 assert before['whole_scope_complete'] and after['whole_scope_complete']
 old=next(s for s in before['scope_status']if s['scope']=='worker_batch')
 new=next(s for s in after['scope_status']if s['scope']=='worker_batch')
 assert old['completion_elapsed_ns']==new['completion_elapsed_ns']==9
 assert new['observed_events']>old['observed_events']


@pytest.mark.parametrize('samples',[
 [(100,True)],[(100,True),(2_000_000,False)],
 [(2_000_000,True),(3_000_000,False)],
])
def test_reappearing_worker_does_not_reuse_earlier_empty_receipt(samples):
 p,term=packet()
 for stamp,active in samples:append_worker_sample(p,term,stamp,active)
 r=result(p);worker=next(s for s in r['scope_status']if s['scope']=='worker_batch')
 assert not worker['termination_verified']and not r['whole_scope_complete']
 if any(active and stamp>1_000_010 for stamp,active in samples):assert r['activation_violations']


def test_late_first_empty_worker_receipt_does_not_infer_earlier_cleanup():
 p,term=packet();rows=p['recordings'][0]['capture']['records']
 rows[:]=[r for r in rows if r['payload'].get('point')!='GPUModelRunner.finish_requests']
 append_worker_sample(p,term,2_000_000,False)
 r=result(p);assert not next(s for s in r['scope_status']if s['scope']=='worker_batch')['termination_verified']
 assert not r['whole_scope_complete']


@pytest.mark.parametrize('states,expected',[
 ([(1,False),(9,False)],1), ([(1,False),(5,True),(9,False)],9),
 ([(1,False),(5,None)],None), ([(1,False),(5,None),(9,False)],9),
 ([],None), ([(1,True)],None),
])
def test_stable_empty_interval_preserves_unknown_and_reappearance(states,expected):
 from guard.adapters.vllm_termination_projection import _settled_completion
 assert _settled_completion(states)==expected


@pytest.mark.parametrize('kind',['incomplete','missing','other-worker'])
def test_worker_with_unqualified_mapping_resets_or_blocks_completion(kind):
 p,term=packet();append_worker_sample(p,term,100,None)
 rows=p['recordings'][0]['capture']['records'];row=next(r for r in rows if r['payload'].get('phase')=='STATE_SNAPSHOT')
 row['payload']['states'][0]['worker_mapping_complete']=False
 if kind=='missing':row['payload']['states']=[]
 if kind=='other-worker':
  other=deepcopy(p['recordings'][0]);header=deepcopy(rows[0]);header['payload']['context']['pid']=43
  other['capture']['records']=[header,deepcopy(row),deepcopy(rows[-1])];p['recordings'].append(other)
 r=result(p);assert not r['whole_scope_complete'] and not next(s for s in r['scope_status']if s['scope']=='worker_batch')['termination_verified']


@pytest.mark.parametrize('recovery',[200,2_000_000])
def test_after_unknown_only_new_qualified_empty_sample_can_complete(recovery):
 p,term=packet();append_worker_sample(p,term,100,None)
 row=next(r for r in p['recordings'][0]['capture']['records']if r['payload'].get('phase')=='STATE_SNAPSHOT')
 row['payload']['states'][0]['worker_mapping_complete']=False
 append_worker_sample(p,term,recovery,False);r=result(p)
 worker=next(s for s in r['scope_status']if s['scope']=='worker_batch')
 assert worker['termination_verified'] is (recovery==200)
 assert worker['completion_elapsed_ns']==recovery-10


def retired_checkpoint_packet():
 from guard.executors.lifecycle_checkpoint import canonical
 p,c=with_retired_import_process();rows=c['records'];rows.insert(-1,dict(event='CHECKPOINT',payload=dict(schema='guard-native-checkpoint-ack/1',session=p['session'],prefix_seq=2,prefix_sha256=''),monotonic_ns=BASE-85))
 previous='0'*64
 for seq,row in enumerate(rows):
  row.update(seq=seq,previous_sha256=previous)
  if row['event']=='CHECKPOINT':row['payload'].update(prefix_seq=seq,prefix_sha256=previous)
  previous=sha256(canonical(row)).hexdigest()
 c['provenance']['capture_sha256']=sha256(b''.join(canonical(row)for row in rows)).hexdigest()
 return p,c


def test_retired_checkpoint_heartbeat_is_not_request_work_and_preserves_original():
 p,c=retired_checkpoint_packet();original=deepcopy(c);r=result(p)
 assert r['whole_scope_complete'] and len(r['historical_import_only_recordings'])==1
 assert c==original and not c['process_context_verified']


@pytest.mark.parametrize('damage',['session','schema','seq','prefix','chain','clock','native','term','gap','late_end'])
def test_retired_checkpoint_cannot_hide_invalid_marker_chain_or_work(damage):
 p,c=retired_checkpoint_packet();row=c['records'][2]
 if damage=='session':row['payload']['session']='b'*32
 if damage=='schema':row['payload']['schema']='other/1'
 if damage=='seq':row['payload']['prefix_seq']=1
 if damage=='prefix':row['payload']['prefix_sha256']='f'*64
 if damage=='chain':row['previous_sha256']='f'*64
 if damage=='clock':row['monotonic_ns']=BASE-101
 if damage in {'native','term','gap'}:row['event']={'native':'NATIVE','term':'TERMINATION','gap':'SG_GAP'}[damage]
 if damage=='late_end':c['records'][-1]['monotonic_ns']=BASE+100
 if damage!='chain':
  from guard.executors.lifecycle_checkpoint import canonical
  previous='0'*64
  for item in c['records']:
   item['previous_sha256']=previous;previous=sha256(canonical(item)).hexdigest()
  c['provenance']['capture_sha256']=sha256(b''.join(canonical(item)for item in c['records'])).hexdigest()
 assert not result(p)['historical_import_only_recordings']
 assert not result(p)['whole_scope_complete']
