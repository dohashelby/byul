"""SG profile registration/owned wire boundaries; EXAMPLE metadata only."""
from copy import deepcopy
from pathlib import Path
import hashlib,json
import pytest
from guard.adapters import sglang_request_termination_sensor as sensor,vllm_lifecycle_profiles as profiles,vllm_lifecycle_sensor as base8
from guard.collectors.lifecycle import prepare_assets,load_prepared
from guard.executors.lifecycle_capture import parse_recording,canonical_row
from guard.executors.owned_capture import _read_budget,EXTENDED_RECORDING_PROFILE,LARGE_RECORDING_PROFILE
from tests.test_lifecycle_sensor import CONTEXT,writer_factory
from tests.test_lifecycle_capture import case,external
from tests.test_kubernetes_lifecycle import group,target


def entries(prepared):
 head={'schema':sensor.SCHEMA,'profile':sensor.PROFILE,'commit':sensor.COMMIT,'session':prepared['session'],'settings_sha256':prepared['assets']['settings.json']['sha256'],'sensor_sha256':prepared['assets']['guard_lifecycle_sensor.py']['sha256'],'context':CONTEXT,'producer_authenticated':False}
 values=[('START',head)]
 for name in ('sglang.srt.runtime_context','sglang.srt.managers.scheduler','sglang.srt.managers.tokenizer_manager'):
  values.append(('MODULE',{'module':name,'verified':True,'source_sha256':sensor.SOURCE_SHA[name],'code_sha256':'a'*64}))
 values.extend([('SG_NATIVE',{'point':'TokenizerManager._init_req_state','phase':'RETURN','operation':1,'source_module':'sglang.srt.managers.tokenizer_manager','source_verified':True,'requests':[{'request_sha256':'b'*64,'state_present':True,'kv_registered':None,'kv_allocated':None,'mamba_present':None}],'asgi_request_sha256':'c'*64,'asgi_invocation_sha256':'d'*64,'qualified_single_scheduler':True}),('SG_ASGI',{'phase':'DISCONNECT_RECEIVED','request_sha256':'c'*64,'invocation_sha256':'d'*64}),('SG_GPU_FENCE',{'operation':1,'stream':'forward_stream','requests':['b'*64],'completed':True,'timed_out':False,'qualified_single_scheduler':True}),('END',{'reason':'SEALED'})])
 return values


def raw(values):
 out=[];previous='0'*64
 for seq,(event,payload)in enumerate(values):
  row={'seq':seq,'event':event,'payload':payload,'monotonic_ns':seq+10,'previous_sha256':previous};line=canonical_row(row);out.append(line);previous=hashlib.sha256(line).hexdigest()
 return b''.join(out)


def prepared(tmp_path):return prepare_assets(tmp_path/'assets',Path(__file__).resolve().parents[2],profile=sensor.PROFILE)


def test_prepare_exact_sg_oci_reference_base8_and_closed_wire(tmp_path):
 p=prepared(tmp_path);assert load_prepared(tmp_path/'assets/manifest.json')==p
 assert p['public_commit']==sensor.COMMIT and len(p['public_commit'])==64 and p['source_sha256']==sensor.SOURCE_SHA
 assert (tmp_path/'assets/guard_lifecycle_base.py').read_bytes()==Path(base8.__file__).read_bytes()
 assert hashlib.sha256((tmp_path/'assets/guard_sglang_launcher.py').read_bytes()).hexdigest()=='99856447cc643b4597851b6e330d6744e6ca11d3ff0c94493b6df07a8a5b7e4a'
 assert set(p['assets'])=={'sitecustomize.py','settings.json','guard_lifecycle_base.py','guard_lifecycle_sensor.py','guard_sglang_launcher.py'}
 rows,summary=parse_recording(raw(entries(p)),p);assert rows[-1]['event']=='END'and summary['recording_closed']and summary['recording_end']=='SEALED'and not summary['incomplete_final_row']
 assert profiles.point_sources(sensor.PROFILE)['common.release_kv_cache']=='sglang.srt.mem_cache.common'
 from guard.executors.lifecycle_session import request_index
 index=request_index([{'capture':{'records':rows,'provenance':{'owner':{'id':'EXAMPLE-container'}}}}]);assert index['stored_record_index_complete']
 group,=index['domains'].values();assert group['assignments'][0]['external_sha256']=='c'*64 and group['assignments'][0]['internal_sha256']=='b'*64


@pytest.mark.parametrize('damage',['git-commit','vllm-module','bad-source','private-key','unknown-phase','source-order','config-source','after-end'])
def test_source_schema_private_and_event_order_mismatches_refused(tmp_path,damage):
 p=prepared(tmp_path);v=entries(p)
 if damage=='git-commit':v[0][1]['commit']=base8.COMMIT
 elif damage=='vllm-module':v[1][1]['module']='vllm.v1.engine.core'
 elif damage=='bad-source':v[1][1]['source_sha256']='f'*64
 elif damage=='private-key':v[-2][1]['private_debug']='EXAMPLE_PRIVATE'
 elif damage=='unknown-phase':v[-3][1]['phase']='EXAMPLE_PRIVATE'
 elif damage=='source-order':v[3],v[4]=v[4],v[3]
 elif damage=='config-source':v.pop(1)
 elif damage=='after-end':v.append(deepcopy(v[-3]))
 with pytest.raises(ValueError):parse_recording(raw(v),p)


def test_incomplete_tail_is_retained_as_incomplete_not_a_closed_session(tmp_path):
 p=prepared(tmp_path);r=raw(entries(p));rows,summary=parse_recording(r[:-7],p)
 assert not summary['recording_closed']and summary['incomplete_final_row']and summary['discarded_tail_bytes']>0
 assert rows[-1]['event']=='SG_GPU_FENCE'


def test_4m_transport_is_explicit_and_does_not_expand_old_2m():
 _read_budget(4*1024**2,LARGE_RECORDING_PROFILE);_read_budget(2*1024**2,EXTENDED_RECORDING_PROFILE)
 for n,profile in [(2*1024**2+1,EXTENDED_RECORDING_PROFILE),(4*1024**2+1,LARGE_RECORDING_PROFILE),(True,LARGE_RECORDING_PROFILE),(4*1024**2,None)]:
  with pytest.raises(ValueError):_read_budget(n,profile)


def test_kubernetes_reader_sg_version_and_explicit_4m_profile(tmp_path,group,target):
 from guard.executors.kubernetes_lifecycle_capture import KubernetesLifecycleReader,KubernetesCaptureError
 p=prepared(tmp_path);environment=group.env.model_copy(deep=True);environment.engine.name='sglang';environment.engine.version='0.5.22';environment=environment.seal();target=deepcopy(target);target['environment_ref']=environment.ref().model_dump(mode='json')
 reader=KubernetesLifecycleReader(target,environment,p);assert reader.file_read_limit==4*1024**2 and reader.file_read_profile==LARGE_RECORDING_PROFILE
 assert reader.image_check({}, {})is False
 from guard.executors.session_seal import SESSION_SEALER,LARGE_SESSION_SEALER
 assert hashlib.sha256(SESSION_SEALER.encode()).hexdigest()=='7b40e8da40cbd7607954edb01d6d7c3b5ae257925468c158f1cdfe7ab8c698ec'
 assert reader.programs['seal']==LARGE_SESSION_SEALER and reader.programs['seal']!=SESSION_SEALER
 assert reader._program(reader.binding['engine']['container'],['python3','-c',LARGE_SESSION_SEALER,p['session'],str(4*1024**2)],4096)=='seal'
 with pytest.raises(KubernetesCaptureError):reader._program(reader.binding['engine']['container'],['python3','-c',SESSION_SEALER,p['session'],str(4*1024**2)],4096)
 environment.engine.version='0.5.21'
 with pytest.raises(KubernetesCaptureError):KubernetesLifecycleReader(target,environment,p)


def test_explicit_sglang_http_profile_reuses_fixed_close_contract_and_preserves_vllm_gate():
 from guard.runtime.kubernetes_http import KubernetesHTTP,KubernetesBindingError,TERMINATION_PROFILE,SGLANG_TERMINATION_PROFILE
 from tests.test_kubernetes_request_binding import env as make_env,target as make_target
 def inputs(name,version):
  e=make_env().model_copy(deep=True);e.engine.name=name;e.engine.version=version;e=e.seal();t=make_target();t['environment_ref']=e.ref().model_dump(mode='json');return e,t
 e,t=inputs('sglang','0.5.22');http=KubernetesHTTP(t,e,duration=30,runner=lambda *a,**kw:None,profile=SGLANG_TERMINATION_PROFILE)
 request={'id':'lif10-before','mode':'json','method':'POST','url':t['engine']['trusted_url']+'/v1/completions','headers':{'X-Request-Id':'byul-lif10-'+'a'*32+'-before'},'body':{'model':http.api_model,'prompt':'hello','max_tokens':1,'temperature':0,'stream':False}}
 http._termination_request(request,10)
 assert http.report()['termination_contract_version']==1 and http.report()['native_termination_verified']is False
 for name,version,profile in [('sglang','0.5.21',SGLANG_TERMINATION_PROFILE),('vllm','0.31.0',SGLANG_TERMINATION_PROFILE),('sglang','0.5.22',TERMINATION_PROFILE)]:
  e,t=inputs(name,version)
  with pytest.raises(KubernetesBindingError):KubernetesHTTP(t,e,duration=30,runner=lambda *a,**kw:None,profile=profile)


@pytest.mark.parametrize('damage',['negative-clock','unverified-source','unverified-code'])
def test_negative_start_clock_and_unverified_module_private_hash_fields_are_refused(tmp_path,damage):
 p=prepared(tmp_path);v=entries(p)
 if damage=='negative-clock':
  data=raw(v);lines=data.splitlines();r=json.loads(lines[0]);r['monotonic_ns']=-1;data=canonical_row(r)
 else:
  v[1][1]['verified']=False;v[1][1]['source_sha256'if damage=='unverified-source'else'code_sha256']='EXAMPLE_PRIVATE';data=raw(v[:2]+[v[-1]])
 with pytest.raises(ValueError):parse_recording(data,p)
