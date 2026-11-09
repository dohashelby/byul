"""EXAMPLE containerd/kernel transports; no node, image pull or GPU mutation."""
from copy import deepcopy
import hashlib,json,time,types
import pytest
from guard.executors import node_image_proof as m

@pytest.fixture(autouse=True)
def fixed_test_tool(monkeypatch):monkeypatch.setattr(m,'verify_grpcurl_binary',lambda:None)

CID='a'*64
KERNEL={'machine_id':'b'*32,'boot_id':'cccccccc-cccc-cccc-cccc-cccccccccccc','system_uuid':'dddddddd-dddd-dddd-dddd-dddddddddddd','architecture':'amd64','os':'linux'}
NODE={'kind':'Node','metadata':{'name':'node-one','uid':'eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee'},'status':{'nodeInfo':{'machineID':KERNEL['machine_id'],'bootID':KERNEL['boot_id'],'systemUUID':KERNEL['system_uuid'],'architecture':'amd64','operatingSystem':'linux','containerRuntimeVersion':'containerd://2.2.0'}}}

def raw(value):return json.dumps(value,sort_keys=True,separators=(',',':')).encode()
def checksum(value):return 'sha256:'+hashlib.sha256(value).hexdigest()

class World:
 def __init__(self,damage=None):
  self.damage=damage;self.calls=[];self.container_count=0;self.blobs={};self.private_hashes=[]
  diff=['sha256:'+'1'*64,'sha256:'+'2'*64];self.chain='sha256:'+m.sha((diff[0]+' '+diff[1]).encode())
  config={'architecture':'arm64'if damage=='architecture'else'amd64','os':'linux','rootfs':{'type':'layers','diff_ids':diff},'config':{'Env':['SECRET_TOKEN=EXAMPLE_PRIVATE']}}
  self.config=self.blob(config)
  manifest={'schemaVersion':2,'mediaType':'application/vnd.oci.image.manifest.v1+json','config':{'mediaType':'application/vnd.oci.image.config.v1+json','digest':self.config,'size':len(self.blobs[self.config])},'layers':[{'digest':'sha256:'+'3'*64},{'digest':'sha256:'+'4'*64}]}
  self.manifest=self.blob(manifest)
  desc={'mediaType':manifest['mediaType'],'digest':self.manifest,'size':len(self.blobs[self.manifest]),'platform':{'architecture':'amd64','os':'linux'}}
  index={'schemaVersion':2,'mediaType':'application/vnd.oci.image.index.v1+json','manifests':[desc,deepcopy(desc)]if damage=='ambiguous-platform'else[desc]};self.root=self.blob(index)
  self.engine={'pod_uid':'pod-uid','container_id':'containerd://'+CID,'node':'node-one','namespace':'serving-lab','pod':'engine-one','container':'engine','image':'repo/engine@'+self.root,'image_id':self.config}
 def blob(self,value):
  b=raw(value);d=checksum(b);self.blobs[d]=b;return d
 def process(self,pid):
  if self.damage=='root-mount':raise ValueError('EXAMPLE_OTHER_ROOT_MOUNT')
  return dict(pid=pid,start_ticks=123,pid_namespace=456,root_mount_identity_sha256='5'*64,cgroup='0::/kubepods/'+('0'*64 if self.damage=='cgroup'else CID))
 def run(self,argv,**kw):
  self.calls.append((argv,kw));assert kw['env_extra']=={}and 0<kw['timeout']<=5
  if argv[:3]==['sudo','-n',m.GRPCURL_BINARY]:
   from pathlib import Path
   assert (Path(argv[argv.index('-import-path')+1])/argv[argv.index('-proto')+1]).read_text()==m.MOUNTS_PROTO and argv[-1]=='containerd.services.snapshots.v1.Snapshots/Mounts' and argv[-2]=='unix:///run/containerd/containerd.sock' and '-unix'in argv
   return {'returncode':0,'stdout_bytes':raw({'mounts':[{'type':'overlay','source':'overlay','options':['lowerdir=/example/lower','upperdir=/example/upper','workdir=/example/work']}]}),'capture':{'complete':True,'bounded_at_source':True,'timed_out':False,'truncated':False}}
  assert argv[:5]==['sudo','-n','ctr','-n','k8s.io']
  args=argv[5:]
  if args[:2]==['containers','info']:
   self.container_count+=1;v={'ID':CID,'Image':'repo/engine@'+self.root,'Snapshotter':'overlayfs','SnapshotKey':CID,'Labels':{'io.kubernetes.pod.uid':'pod-uid','io.kubernetes.pod.name':'engine-one','io.kubernetes.pod.namespace':'serving-lab','io.kubernetes.container.name':'engine'},'Spec':{'process':{'env':['SECRET_TOKEN=EXAMPLE_PRIVATE']}}}
   if self.damage=='pod':v['Labels']['io.kubernetes.pod.uid']='different-pod'
   if self.damage=='container-change'and self.container_count==2:v['SnapshotKey']='different-snapshot'
   if self.damage=='unsafe-name':v['Image']='--malicious option'
   b=raw(v);self.private_hashes.append(m.sha(b))
  elif args[:2]==['images','list']:
   line='repo/engine@'+self.root+' application/vnd.oci.image.index.v1+json '+self.root+' 1.0MiB linux/amd64 labels\n';b=('REF TYPE DIGEST SIZE PLATFORMS LABELS\n'+line+(line if self.damage=='ambiguous-image'else'')).encode()
  elif args[:2]==['content','get']:
   b=self.blobs[args[2]]
   if self.damage=='content-change'and args[2]==self.manifest:b+=b' '
  elif args[:1]==['snapshots']:
   b=raw({'Name':CID,'Parent':'sha256:'+'0'*64 if self.damage=='snapshot'else self.chain,'Kind':'Active'})
  elif args==['tasks','list']:b=('TASK PID STATUS\n'+CID+' 77 '+('STOPPED'if self.damage=='stopped-task'else'RUNNING')+'\n').encode()
  else:raise AssertionError(argv)
  return {'returncode':0,'stdout_bytes':b,'stderr_bytes':b'EXAMPLE_PRIVATE_STDERR','capture':{'complete':self.damage!='partial','bounded_at_source':True,'timed_out':False,'truncated':False,'pipes_closed':True,'read_errors':[]}}
 def collect(self,node=None):return m.collect_local_node_image_proof({'engine':self.engine},NODE if node is None else node,runner=self.run,kernel=KERNEL,proc_reader=self.process)


def test_index_manifest_config_snapshot_task_chain_and_secret_free_receipt():
 w=World();proof=w.collect();r=proof.public_receipt();assert proof.matches(w.engine)and r['root_digest']==w.root and r['manifest_digest']==w.manifest and r['config_digest']==w.config and r['rootfs_chain_id']==w.chain and r['snapshot_chain_verified']
 assert r['reported_reference_kind']=='oci-config-sha256'and r['data_origin']=='EXAMPLE'
 assert 'EXAMPLE_PRIVATE'not in json.dumps(r)and 'SECRET_TOKEN'not in json.dumps(r)and repr(proof)=='<NodeImageProof>'
 assert not any(h in json.dumps(r)for h in w.private_hashes)
 other=deepcopy(w.engine);other['container_id']='containerd://'+'0'*64;assert not proof.matches(other)
 assert all(args[:3]==['sudo','-n',m.GRPCURL_BINARY]or args[5]in {'containers','images','content','snapshots','tasks'}for args,_ in w.calls)
 with pytest.raises(ValueError):m.NodeImageProof(w.engine,r)


@pytest.mark.parametrize('damage',['architecture','ambiguous-platform','ambiguous-image','content-change','snapshot','stopped-task','cgroup','pod','container-change','unsafe-name','partial','root-mount'])
def test_each_source_identity_or_content_boundary_refuses_proof(damage):
 with pytest.raises(ValueError,match='NODE_IMAGE_PROOF_UNVERIFIED'):World(damage).collect()


@pytest.mark.parametrize('field',['machineID','bootID','systemUUID','architecture','operatingSystem'])
def test_another_node_or_kernel_cannot_supply_local_evidence(field):
 w=World();node=deepcopy(NODE);node['status']['nodeInfo'][field]='other'
 with pytest.raises(ValueError):w.collect(node)
 assert w.calls==[]


def test_kubernetes_image_check_is_opt_in_and_rechecks_owner_and_node(monkeypatch):
 from guard.executors.kubernetes_lifecycle_capture import KubernetesLifecycleReader
 w=World();reader=object.__new__(KubernetesLifecycleReader);reader.binding={'engine':w.engine};reader.node_image_verification=None
 assert reader.image_check({}, {})is False and not w.calls
 reader.node_image_verification=m.PROFILE;reader.injected=True;reader.runner=w.run;reader.deadline=time.monotonic()+120;reader.gap=False;reader.node_image_receipts=[]
 owner={'container_id':w.engine['container_id'],'pod_uid':'pod-uid'};reader._owner=lambda timeout:owner;reader._request=lambda *a,**kw:raw(NODE)
 monkeypatch.setattr(m,'kernel_identity',lambda:KERNEL)
 original=m.collect_local_node_image_proof
 def collect(binding,node,**kw):return original(binding,node,runner=w.run,kernel=KERNEL,proc_reader=w.process)
 monkeypatch.setattr(m,'collect_local_node_image_proof',collect)
 assert reader.image_check({'owner':owner},{'container':'engine','image':w.engine['image']})is True and len(reader.node_image_receipts)==1
 assert reader.image_check({'owner':{'container_id':'other'}},{'container':'engine','image':w.engine['image']})is False and reader.gap


@pytest.mark.parametrize('selected',[False,True])
@pytest.mark.parametrize('lab,injected,origin',[(False,True,'EXAMPLE'),(True,False,'DECLARED'),(False,False,'DECLARED')])
def test_public_capture_cli_forwards_explicit_node_proof_and_seal_before_capture(tmp_path,monkeypatch,selected,lab,injected,origin):
 from importlib.util import spec_from_file_location,module_from_spec
 from pathlib import Path
 from tests.builders import environment
 from guard.collectors import lifecycle
 from guard.executors import kubernetes_lifecycle_capture,lifecycle_session
 root=Path(__file__).resolve().parents[2];spec=spec_from_file_location('_public_node_capture_cli',root/'runtime/tools/update_engine_link.py');cli=module_from_spec(spec);spec.loader.exec_module(cli)
 from guard.models import DataOrigin
 e=environment(origin=DataOrigin.DECLARED);(tmp_path/'env.json').write_text(e.model_dump_json());(tmp_path/'target.json').write_text(json.dumps({'lab':lab}))
 monkeypatch.setattr(lifecycle,'load_prepared',lambda path:{'profile':'vllm-native-lifecycle-metadata/23','session':'a'*32})
 events=[]
 class Reader:
  def __init__(self,*a,**kw):
   assert kw==({'duration':30,'node_image_verification':m.PROFILE}if selected else{'duration':30})
   self.binding={'engine':{'container':'engine','image':'repo@sha256:'+'f'*64}};self.injected=injected
   self.owner_identity=self.read_file=self.image_check=None
  def snapshot(self):events.append('snapshot')
  def seal_session(self):events.append('seal');return {'sealed':True}
  def report(self):return {'node_runtime_image_verified':False,'collection_gap':False}
 monkeypatch.setattr(kubernetes_lifecycle_capture,'KubernetesLifecycleReader',Reader)
 def capture(*a,**kw):events.append('capture');return {'recordings':[],'security':'UNKNOWN'}
 monkeypatch.setattr(lifecycle_session,'capture_session',capture)
 args=['capture','--environment',str(tmp_path/'env.json'),'--target',str(tmp_path/'target.json'),'--prepared',str(tmp_path/'prepared.json'),'--output',str(tmp_path/'out.json')]
 if selected:args+=['--node-image-verification',m.PROFILE,'--seal']
 assert cli.main(args)==0 and events==(['snapshot','seal','capture']if selected else['snapshot','capture'])
 result=json.loads((tmp_path/'out.json').read_text());assert result['transport']['node_runtime_image_verified']is False and ('session_sealed'in result)is selected and result['data_origin']==origin


def test_actual_root_mount_directory_objects_must_match_selected_snapshot(tmp_path):
 import os,re,stat
 namespace=dict(os=os,re=re,stat=stat,hashlib=hashlib,json=json);exec(m.MOUNT_COMPARISON,namespace)
 paths={}
 for name in ('lower','upper','work','unused-upper'):
  p=tmp_path/name;p.mkdir();paths[name]=str(p)
 def mount(upper):return {'type':'overlay','source':'overlay','options':['lowerdir='+paths['lower'],'upperdir='+paths[upper],'workdir='+paths['work']]}
 actual=mount('upper');line='40 25 0:30 / / ro,relatime - overlay overlay rw,'+','.join(actual['options'])+'\n'
 checksum=namespace['root_mount_identity'](actual,line);assert len(checksum)==64
 with pytest.raises(ValueError):namespace['root_mount_identity'](mount('unused-upper'),line)
 assert str(tmp_path)not in checksum


def test_mounts_descriptor_declares_only_read_rpc_and_does_not_call_activation():
 assert 'rpc Mounts('in m.MOUNTS_PROTO and m.MOUNTS_PROTO.count('rpc ')==1
 for forbidden in ('Prepare','Activate','Remove','Commit','Cleanup'):assert forbidden not in m.MOUNTS_PROTO
 assert 'root_mount_identity' in m.PROCESS_READER and 'mountinfo'in m.PROCESS_READER


def test_kernel_mount_escapes_are_decoded_without_persisting_paths(tmp_path):
 import os,re,stat
 namespace=dict(os=os,re=re,stat=stat,hashlib=hashlib,json=json);exec(m.MOUNT_COMPARISON,namespace)
 paths=[]
 for name in ('lower space','upper space','work space'):
  p=tmp_path/name;p.mkdir();paths.append(str(p))
 expected={'type':'overlay','source':'overlay','options':['lowerdir='+paths[0],'upperdir='+paths[1],'workdir='+paths[2]]}
 escaped=[v.replace(' ',r'\040')for v in expected['options']]
 line='40 25 0:30 / / ro - overlay overlay rw,'+','.join(escaped)+'\n'
 assert len(namespace['root_mount_identity'](expected,line))==64


@pytest.mark.parametrize('error,expected',[
 (PermissionError('/EXAMPLE_PRIVATE_PATH'),'NODE_IMAGE_READ_PERMISSION_DENIED'),
 (FileNotFoundError('/EXAMPLE_PRIVATE_PATH'),'NODE_IMAGE_INPUT_MISSING'),
 (ValueError('NODE_IMAGE_GRPCURL_UNVERIFIED'),'NODE_IMAGE_GRPCURL_UNVERIFIED'),
 (ValueError('EXAMPLE_PRIVATE_TOKEN'),'NODE_IMAGE_PROOF_UNVERIFIED'),
 (RuntimeError('NODE_IMAGE_READ_PERMISSION_DENIED'),'NODE_IMAGE_PROOF_UNVERIFIED')])
def test_public_failure_codes_are_fixed_literals_without_exception_body(error,expected):
 assert m.failure_code(error)==expected and 'EXAMPLE_PRIVATE'not in m.failure_code(error)


def test_local_identity_permission_is_retained_as_safe_reader_gap(monkeypatch):
 from guard.executors.kubernetes_lifecycle_capture import KubernetesLifecycleReader
 reader=object.__new__(KubernetesLifecycleReader);w=World();reader.binding={'engine':w.engine}
 reader.node_image_verification=m.PROFILE;reader.injected=False;reader.deadline=time.monotonic()+30
 owner={'pod_uid':'pod-uid'};reader._owner=lambda _:owner;reader._request=lambda *a,**kw:raw(NODE)
 def denied(*a,**kw):raise PermissionError('EXAMPLE_PRIVATE_PATH')
 monkeypatch.setattr(m,'kernel_identity',denied)
 assert reader.image_check({'owner':owner},{'container':'engine','image':w.engine['image']})is False
 assert reader.gap and reader.node_image_proof_failures==1 and reader.node_image_failure_codes=={'NODE_IMAGE_READ_PERMISSION_DENIED'}


def _snapshot_mount_fixture(tmp_path):
 root=tmp_path/'snapshots'
 for index in range(1,71):
  (root/str(index)/'fs').mkdir(parents=True)
 (root/'70'/'work').mkdir()
 absolute=[str(root/str(i)/'fs')for i in range(1,70)]
 expected={'type':'overlay','source':'overlay','options':['lowerdir='+':'.join(absolute),'upperdir='+str(root/'70'/'fs'),'workdir='+str(root/'70'/'work')]}
 relative=['lowerdir='+':'.join(str(i)+'/fs'for i in range(1,70)),*expected['options'][1:]]
 return expected,relative


def test_containerd_long_lowerdir_representation_is_descriptor_anchored(tmp_path):
 import os,re,stat,subprocess,sys
 expected,relative=_snapshot_mount_fixture(tmp_path)
 line='40 25 0:30 / / ro - overlay overlay rw,'+','.join(relative)+'\n'
 # Run the same embedded mount comparison in an independent Python process,
 # whose cwd deliberately has no relationship to the snapshot root.
 code='import os,re,stat,hashlib,json,sys\n'+m.MOUNT_COMPARISON+'\nv=json.load(sys.stdin);print(root_mount_identity(v[0],v[1]))'
 result=subprocess.run([sys.executable,'-I','-S','-B','-c',code],input=json.dumps([expected,line]),text=True,capture_output=True,cwd='/',timeout=5,check=True)
 ns=dict(os=os,re=re,stat=stat,hashlib=hashlib,json=json);exec(m.MOUNT_COMPARISON,ns)
 absolute='40 25 0:30 / / ro - overlay overlay rw,'+','.join(expected['options'])+'\n'
 assert result.stdout.strip()==ns['root_mount_identity'](expected,absolute)
 assert str(tmp_path)not in result.stdout


@pytest.mark.parametrize('damage',['traversal','dot','empty','duplicate','leading-zero','absolute-mixed','reorder','missing','upper','work','expected-relative','expected-other-root','duplicate-option'])
def test_relative_lowerdir_rejects_ambiguous_or_unbound_paths(tmp_path,damage):
 import os,re,stat
 expected,options=_snapshot_mount_fixture(tmp_path)
 lowers=options[0].removeprefix('lowerdir=').split(':')
 if damage=='traversal':lowers[0]='../1/fs'
 elif damage=='dot':lowers[0]='./1/fs'
 elif damage=='empty':lowers[0]=''
 elif damage=='duplicate':lowers[1]=lowers[0]
 elif damage=='leading-zero':lowers[0]='01/fs'
 elif damage=='absolute-mixed':lowers[0]=expected['options'][0].removeprefix('lowerdir=').split(':')[0]
 elif damage=='reorder':lowers[0],lowers[1]=lowers[1],lowers[0]
 elif damage=='missing':lowers.pop()
 elif damage=='upper':options[1]=options[1].replace('/70/fs','/69/fs')
 elif damage=='work':options[2]=options[2].replace('/70/work','/69/fs')
 elif damage=='expected-relative':expected['options'][0]='lowerdir=1/fs'
 elif damage=='expected-other-root':expected['options'][2]=expected['options'][2].replace('/70/work','/69/fs')
 elif damage=='duplicate-option':options.append(options[1])
 options[0]='lowerdir='+':'.join(lowers)
 line='40 25 0:30 / / ro - overlay overlay rw,'+','.join(options)+'\n'
 ns=dict(os=os,re=re,stat=stat,hashlib=hashlib,json=json);exec(m.MOUNT_COMPARISON,ns)
 with pytest.raises(ValueError):ns['root_mount_identity'](expected,line)
