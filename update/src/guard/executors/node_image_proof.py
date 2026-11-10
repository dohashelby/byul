"""Opt-in local-node containerd ancestry proof; all operations are read-only.

Raw OCI config/Spec/environment remain in this process. Public receipts contain
only selected identity fields and digests. The default Kubernetes reader still
supplies no node image proof.
"""
from __future__ import annotations
from copy import deepcopy
import hashlib,json,os,platform,re,stat,sys,time,tempfile
from pathlib import Path
from guard.runtime.bounded import capture_pipes,strict_json

PROFILE='local-containerd-v1'
MEDIA_INDEX={'application/vnd.oci.image.index.v1+json','application/vnd.docker.distribution.manifest.list.v2+json'}
MEDIA_MANIFEST={'application/vnd.oci.image.manifest.v1+json','application/vnd.docker.distribution.manifest.v2+json'}
CAP=2*1024**2
_PROOF_KEY=object()
GRPCURL_BINARY='/usr/local/bin/grpcurl'
GRPCURL_SHA256='62e2e4315bb70fab2e27f86c1f7738d09076a097a2dc8e0f701e386251172e40'
MOUNTS_PROTO='''syntax = "proto3";
package containerd.services.snapshots.v1;
service Snapshots { rpc Mounts(ReadMountRequest) returns (ReadMountResponse); }
message ReadMountRequest { string snapshotter = 1; string key = 2; }
message MountView { string type = 1; string source = 2; string target = 3; repeated string options = 4; }
message ReadMountResponse { repeated MountView mounts = 1; }
'''
MOUNT_COMPARISON=r'''
def mount_identity(value):
 if type(value)is not dict or value.get('type')!='overlay'or value.get('source')!='overlay'or type(value.get('options'))is not list or len(value['options'])>512:raise ValueError()
 paths={}
 for option in value['options']:
  if type(option)is not str or len(option)>131072:raise ValueError()
  key,sep,child=option.partition('=')
  if key not in {'lowerdir','upperdir','workdir'}:continue
  if not sep or key in paths:raise ValueError()
  paths[key]=child.split(':')if key=='lowerdir'else[child]
 if set(paths)!={'lowerdir','upperdir','workdir'}:raise ValueError()
 objects={}
 for key,items in paths.items():
  if not 1<=len(items)<=256:raise ValueError()
  values=[]
  for item in items:
   if not item.startswith('/')or '\x00'in item or '..'in item.split('/'):raise ValueError()
   s=os.stat(item)
   if not stat.S_ISDIR(s.st_mode):raise ValueError()
   values.append([s.st_dev,s.st_ino])
  objects[key]=values
 return {'type':'overlay','source':'overlay','objects':objects}
def root_mount_identity(expected,lines):
 roots=[]
 def unescape(value):return re.sub(r'\\([0-7]{3})',lambda m:chr(int(m[1],8)),value)
 for line in lines.splitlines():
  parts=line.split();separator=parts.index('-')
  if parts[4]!='/':continue
  roots.append({'type':parts[separator+1],'source':unescape(parts[separator+2]),'options':[unescape(v)for v in parts[separator+3].split(',')]})
 if len(roots)!=1:raise ValueError()
 expected_identity=mount_identity(expected)
 actual_mount=roots[0]
 # containerd may shorten long lowerdir lists relative to the snapshot root.
 # Normalize only this exact descriptor-anchored representation, never a cwd.
 # containerd 6f589cf core/mount/mount_linux.go compactLowerdirOption/mountAt.
 # Relative lower paths are representation normalization, not independently
 # read kernel lower-directory inodes; trusted runtime ancestry remains required.
 paths={}
 for option in actual_mount['options']:
  key,sep,value=option.partition('=')
  if key not in {'lowerdir','upperdir','workdir'}:continue
  if not sep or key in paths:raise ValueError()
  paths[key]=value
 if set(paths)!={'lowerdir','upperdir','workdir'}:raise ValueError()
 lowers=paths['lowerdir'].split(':')
 if any(not value.startswith('/')for value in lowers):
  expected_paths={option.partition('=')[0]:option.partition('=')[2]for option in expected['options']if option.partition('=')[0]in paths}
  upper=re.fullmatch(r'(/[^\x00:]+/snapshots)/([1-9][0-9]*)/fs',expected_paths['upperdir'])
  if (upper is None or any(part in {'','.','..'}for part in upper[1].split('/')[1:])
      or expected_paths['workdir']!=upper[1]+'/'+upper[2]+'/work'
      or paths['upperdir']!=expected_paths['upperdir']or paths['workdir']!=expected_paths['workdir']
      or not 2<=len(lowers)<=256 or len(set(lowers))!=len(lowers)
      or any(re.fullmatch(r'[1-9][0-9]*/fs',value)is None for value in lowers)):
   raise ValueError()
  expanded=[upper[1]+'/'+value for value in lowers]
  # Exact ordered strings bind every shortened entry to the trusted descriptor.
  if expanded!=expected_paths['lowerdir'].split(':'):raise ValueError()
  actual_mount=dict(actual_mount,options=[('lowerdir='+':'.join(expanded))if option.startswith('lowerdir=')else option for option in actual_mount['options']])
 actual=mount_identity(actual_mount)
 if expected_identity!=actual:raise ValueError()
 return hashlib.sha256(json.dumps(actual,sort_keys=True,separators=(',',':')).encode()).hexdigest()
'''
PROCESS_READER='import hashlib,json,os,re,stat,sys\n'+MOUNT_COMPARISON+r'''
pid=sys.argv[1]
if re.fullmatch(r'[1-9][0-9]{0,9}',pid)is None:raise ValueError()
request=json.loads(sys.stdin.buffer.read(131073))
if type(request)is not dict or set(request)!={'mount'}:raise ValueError()
root='/proc/'+pid
with open(root+'/stat','rb')as f:raw=f.read(8193)
if len(raw)>8192:raise ValueError()
text=raw.decode();values=text[text.rfind(')')+2:].split()
with open(root+'/cgroup','rb')as f:group=f.read(16385)
if len(group)>16384:raise ValueError()
with open(root+'/mountinfo','rb')as f:mounts=f.read(262145)
if len(mounts)>262144:raise ValueError()
identity=root_mount_identity(request['mount'],mounts.decode())
print(json.dumps(dict(pid=int(pid),start_ticks=int(values[19]),pid_namespace=os.stat(root+'/ns/pid').st_ino,cgroup=group.decode(),root_mount_identity_sha256=identity),sort_keys=True,separators=(',',':')))
'''


def sha(raw):return hashlib.sha256(raw).hexdigest()
def digest(value):return type(value)is str and re.fullmatch(r'sha256:[0-9a-f]{64}',value)is not None
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()
def image_digest(value):
    if type(value)is not str:return None
    m=re.fullmatch(r'(?:[a-z0-9][a-z0-9._:/-]*@)?(sha256:[0-9a-f]{64})',value.removeprefix('docker-pullable://'))
    return m[1]if m else None


def verify_grpcurl_binary():
    before=os.stat(GRPCURL_BINARY,follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode)or before.st_uid!=0 or before.st_mode&0o022 or before.st_size!=24567808:raise ValueError('NODE_IMAGE_GRPCURL_UNVERIFIED')
    fd=os.open(GRPCURL_BINARY,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        opened=os.fstat(fd);verifier=hashlib.sha256()
        with os.fdopen(fd,'rb',closefd=False)as stream:
            while chunk:=stream.read(1024*1024):verifier.update(chunk)
        after=os.fstat(fd);entry=os.stat(GRPCURL_BINARY,follow_symlinks=False)
        identity=lambda v:(v.st_dev,v.st_ino,v.st_uid,v.st_mode,v.st_size,v.st_mtime_ns,v.st_ctime_ns)
        if len({identity(v)for v in (before,opened,after,entry)})!=1 or verifier.hexdigest()!=GRPCURL_SHA256:raise ValueError('NODE_IMAGE_GRPCURL_UNVERIFIED')
    finally:os.close(fd)


def failure_code(error):
    """Expose only fixed diagnostic literals, never exception text or paths."""
    if isinstance(error,PermissionError):return 'NODE_IMAGE_READ_PERMISSION_DENIED'
    if isinstance(error,FileNotFoundError):return 'NODE_IMAGE_INPUT_MISSING'
    allowed={'NODE_IMAGE_READ_PERMISSION_DENIED','NODE_IMAGE_INPUT_MISSING',
        'NODE_IMAGE_NODE_IDENTITY_UNBOUND','NODE_IMAGE_LOCAL_PLATFORM_UNSUPPORTED',
        'NODE_IMAGE_GRPCURL_UNVERIFIED','NODE_IMAGE_PROOF_BUDGET_INVALID'}
    if type(error)is ValueError and len(error.args)==1 and type(error.args[0])is str and error.args[0]in allowed:return error.args[0]
    return 'NODE_IMAGE_PROOF_UNVERIFIED'


def kernel_identity():
    if sys.platform!='linux'or platform.machine()not in {'x86_64','amd64'}:raise ValueError('NODE_IMAGE_LOCAL_PLATFORM_UNSUPPORTED')
    return {'machine_id':Path('/etc/machine-id').read_text().strip().lower(),'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip().lower(),
            'system_uuid':Path('/sys/class/dmi/id/product_uuid').read_text().strip().lower(),'architecture':'amd64','os':'linux'}


def validate_node(node,expected_name,kernel):
    try:
        m=node['metadata'];info=node['status']['nodeInfo']
        if(kernel.get('architecture')!='amd64'or kernel.get('os')!='linux'or not re.fullmatch('[0-9a-f-]{36}',kernel.get('system_uuid',''))or node['kind']!='Node'or m['name']!=expected_name or type(m['uid'])is not str or re.fullmatch('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',m['uid'])is None or m.get('deletionTimestamp')
            or not re.fullmatch('[0-9a-f]{32}',kernel['machine_id'])or not re.fullmatch('[0-9a-f-]{36}',kernel['boot_id'])
            or info['machineID'].lower()!=kernel['machine_id']or info['bootID'].lower()!=kernel['boot_id']or info['systemUUID'].lower()!=kernel['system_uuid']
            or info['architecture']!=kernel['architecture']or info['operatingSystem']!=kernel['os']or not info['containerRuntimeVersion'].startswith('containerd://')):raise ValueError
        return {'node':m['name'],'node_uid':m['uid'],'machine_id_sha256':sha(kernel['machine_id'].encode()),'boot_id_sha256':sha(kernel['boot_id'].encode()),'architecture':kernel['architecture'],'os':kernel['os']}
    except (KeyError,TypeError,AttributeError,ValueError):raise ValueError('NODE_IMAGE_NODE_IDENTITY_UNBOUND')from None


class NodeImageProof:
    __slots__=('_scope','_receipt')
    def __init__(self,scope,receipt,*,_key=None):
        if _key is not _PROOF_KEY:raise ValueError('NODE_IMAGE_COLLECTED_PROOF_REQUIRED')
        self._scope=deepcopy(scope);self._receipt=deepcopy(receipt)
    def matches(self,engine):return self._scope=={k:engine.get(k)for k in self._scope}
    def public_receipt(self):return deepcopy(self._receipt)
    def __repr__(self):return '<NodeImageProof>'


def collect_local_node_image_proof(binding,node,*,runner=None,kernel=None,proc_reader=None,timeout=30):
    """Verify CID→named image→content chain→rootfs snapshot on this exact node.

    Injected runner/kernel/proc fixtures are CPU transport examples. Callers keep
    that origin. No arbitrary argv or operator-supplied successful booleans are
    accepted as a substitute for these reads.
    """
    run=runner or capture_pipes;local=kernel_identity()if kernel is None else kernel
    if type(timeout)not in (int,float)or not 0<timeout<=30:raise ValueError('NODE_IMAGE_PROOF_BUDGET_INVALID')
    deadline=time.monotonic()+timeout
    try:
        engine=binding['engine'];scope={k:engine[k]for k in ('pod_uid','container_id','node','namespace','pod','container','image','image_id')}
        node_scope=validate_node(node,engine['node'],local);cid=engine['container_id'].removeprefix('containerd://')
        if not re.fullmatch('[0-9a-f]{64}',cid)or not engine['container_id'].startswith('containerd://'):raise ValueError
        declared=image_digest(engine['image']);reported=image_digest(engine['image_id'])
        if declared is None or reported is None:raise ValueError
        prefix=['sudo','-n','ctr','-n','k8s.io'];reads=[]
        def pipe(argv,cap=CAP,input_bytes=None):
            remaining=min(5,deadline-time.monotonic())
            if remaining<=0:raise ValueError
            kwargs={'timeout':remaining,'env_extra':{},'max_stdout_bytes':cap,'max_stderr_bytes':4096}
            if input_bytes is not None:kwargs['input_bytes']=input_bytes
            result=run(argv,**kwargs)
            p=result.get('capture',{});raw=result.get('stdout_bytes')
            if(result.get('returncode')!=0 or type(raw)is not bytes or not 0<len(raw)<=cap or p.get('complete')is not True or p.get('bounded_at_source')is not True or p.get('timed_out')is not False or p.get('truncated')is not False or p.get('pipes_closed')is False or p.get('read_errors')):raise ValueError
            if len(reads)>=16:raise ValueError
            entry={'argv_sha256':sha(canonical(argv)),'bytes':len(raw)}
            if argv[:len(prefix)]!=prefix or argv[len(prefix):len(prefix)+2]!=['containers','info']:entry['stdout_sha256']=sha(raw)
            reads.append(entry);return raw
        def read(args):return pipe(prefix+args)
        def obj(args):return strict_json(read(args),limit=CAP)
        def container(value):
            labels=value['Labels'];name=value['Image'];snapshot=value['SnapshotKey'];snapshotter=value['Snapshotter']
            if(value['ID']!=cid or labels.get('io.kubernetes.pod.uid')!=engine['pod_uid']or labels.get('io.kubernetes.pod.name')!=engine['pod']or labels.get('io.kubernetes.pod.namespace')!=engine['namespace']or labels.get('io.kubernetes.container.name')!=engine['container']
                or type(name)is not str or not re.fullmatch('[a-z0-9][a-z0-9._:/@-]{0,511}',name)or type(snapshot)is not str or not re.fullmatch('[a-zA-Z0-9][a-zA-Z0-9._:-]{0,255}',snapshot)or snapshotter!='overlayfs'):raise ValueError
            return {'id':cid,'image':name,'snapshot':snapshot,'snapshotter':snapshotter}
        before=container(obj(['containers','info',cid]));listing=read(['images','list','name=='+before['image']]).decode('utf-8','strict').splitlines()
        candidates=[line.split()for line in listing[1:]if line.split()and line.split()[0]==before['image']]
        if len(candidates)!=1 or len(candidates[0])<3 or not digest(candidates[0][2]):raise ValueError
        root_digest=candidates[0][2]
        def content(checksum,descriptor=None):
            if not digest(checksum):raise ValueError
            raw=read(['content','get',checksum])
            if 'sha256:'+sha(raw)!=checksum or descriptor is not None and(type(descriptor.get('size'))is not int or descriptor['size']!=len(raw)):raise ValueError
            return strict_json(raw,limit=CAP)
        root=content(root_digest)
        if type(root)is not dict or root.get('schemaVersion')!=2:raise ValueError
        manifest=root;manifest_digest=root_digest;kind=root.get('mediaType')
        if kind in MEDIA_INDEX:
            choices=[d for d in root.get('manifests',[])if type(d)is dict and d.get('platform',{}).get('os')=='linux'and d.get('platform',{}).get('architecture')=='amd64'and d.get('platform',{}).get('variant')in (None,'')]
            if len(choices)!=1 or choices[0].get('mediaType')not in MEDIA_MANIFEST:raise ValueError
            manifest_digest=choices[0]['digest'];manifest=content(manifest_digest,choices[0])
        elif kind not in MEDIA_MANIFEST:raise ValueError
        if declared not in {root_digest,manifest_digest}or manifest.get('schemaVersion')!=2 or manifest.get('mediaType')not in MEDIA_MANIFEST:raise ValueError
        config_descriptor=manifest['config']
        if type(config_descriptor)is not dict or config_descriptor.get('mediaType')not in {'application/vnd.oci.image.config.v1+json','application/vnd.docker.container.image.v1+json'}:raise ValueError
        config_digest=config_descriptor['digest'];config=content(config_digest,config_descriptor)
        if config.get('architecture')!='amd64'or config.get('os')!='linux'or reported not in {root_digest,manifest_digest,config_digest}:raise ValueError
        diff_ids=config['rootfs']['diff_ids'];layers=manifest['layers']
        if config['rootfs'].get('type')!='layers'or type(diff_ids)is not list or not 1<=len(diff_ids)<=256 or not all(digest(v)for v in diff_ids)or type(layers)is not list or len(layers)!=len(diff_ids)or not all(type(v)is dict and digest(v.get('digest'))for v in layers):raise ValueError
        chain=diff_ids[0]
        for value in diff_ids[1:]:chain='sha256:'+sha((chain+' '+value).encode())
        snapshot=obj(['snapshots','--snapshotter','overlayfs','info',before['snapshot']])
        selected_snapshot={k:snapshot.get(k)for k in ('Name','Parent','Kind')}
        if selected_snapshot!={'Name':before['snapshot'],'Parent':chain,'Kind':'Active'}:raise ValueError
        # A live task and its kernel cgroup bind the image association to the
        # running container rather than an unused image-store record.
        tasks=read(['tasks','list']).decode('utf-8','strict').splitlines();selected=[line.split()for line in tasks[1:]if line.split()and line.split()[0]==cid]
        if len(selected)!=1 or len(selected[0])<3 or not re.fullmatch('[1-9][0-9]{0,9}',selected[0][1])or selected[0][2]!='RUNNING':raise ValueError
        pid=int(selected[0][1])
        verify_grpcurl_binary()
        def mounts():
            with tempfile.TemporaryDirectory(prefix='byul-mounts-proto-')as directory:
                path=Path(directory)/'read.proto';path.write_text(MOUNTS_PROTO);path.chmod(0o600)
                response=strict_json(pipe(['sudo','-n',GRPCURL_BINARY,'-plaintext','-unix','-H','containerd-namespace:k8s.io','-import-path',str(directory),'-proto','read.proto','-d',json.dumps({'snapshotter':'overlayfs','key':before['snapshot']},separators=(',',':')),'unix:///run/containerd/containerd.sock','containerd.services.snapshots.v1.Snapshots/Mounts'],cap=262144),limit=262144)
            if type(response)is not dict or set(response)!={'mounts'}or type(response['mounts'])is not list or len(response['mounts'])!=1:raise ValueError
            selected=response['mounts'][0]
            if type(selected)is not dict or set(selected)-{'type','source','target','options'}or selected.get('target','')not in ('','/')or selected.get('type')!='overlay'or selected.get('source')!='overlay'or type(selected.get('options'))is not list:raise ValueError
            return selected
        expected_mount=mounts()
        def process():
            if proc_reader is not None:return proc_reader(pid)
            return strict_json(pipe(['sudo','-n','python3','-I','-S','-B','-c',PROCESS_READER,str(pid)],cap=32768,input_bytes=canonical({'mount':expected_mount})),limit=32768)
        live=process()
        if(live.get('pid')!=pid or type(live.get('start_ticks'))is not int or not 0<live['start_ticks']<2**64 or type(live.get('pid_namespace'))is not int or not 0<live['pid_namespace']<2**64 or not digest('sha256:'+live.get('root_mount_identity_sha256',''))or type(live.get('cgroup'))is not str or not re.search(r'(?<![0-9a-f])'+cid+r'(?![0-9a-f])',live['cgroup'])):raise ValueError
        after=container(obj(['containers','info',cid]));snapshot_after=obj(['snapshots','--snapshotter','overlayfs','info',before['snapshot']])
        if before!=after or {k:snapshot_after.get(k)for k in ('Name','Parent','Kind')}!=selected_snapshot or mounts()!=expected_mount or process()!=live:raise ValueError
        reference_kind='oci-config-sha256'if reported==config_digest else'oci-manifest-sha256'if reported==manifest_digest else'oci-index-sha256'
        receipt={'schema':'guard-node-image-proof/1','profile':PROFILE,'scope_sha256':sha(canonical(scope)),'node':node_scope,
            'root_digest':root_digest,'manifest_digest':manifest_digest,'config_digest':config_digest,'reported_reference_kind':reference_kind,
            'rootfs_chain_id':chain,'container_projection_sha256':sha(canonical(before)),'snapshot_key_sha256':sha(before['snapshot'].encode()),'task':{k:live[k]for k in ('pid','start_ticks','pid_namespace')},
            'reads':reads,'image_chain_verified':True,'snapshot_chain_verified':True,'live_root_mount_verified':True,'root_mount_identity_sha256':live['root_mount_identity_sha256'],'grpcurl_binary_sha256':GRPCURL_SHA256,'mounts_proto_sha256':sha(MOUNTS_PROTO.encode()),'process_reader_sha256':sha(PROCESS_READER.encode()),'mount_descriptor_sha256':sha(canonical(expected_mount)),'observed_monotonic_ns':time.monotonic_ns(),
            'data_origin':'EXAMPLE'if runner is not None or kernel is not None or proc_reader is not None or binding.get('lab')is True else'DECLARED'}
        return NodeImageProof(scope,receipt,_key=_PROOF_KEY)
    except PermissionError:raise ValueError('NODE_IMAGE_READ_PERMISSION_DENIED')from None
    except FileNotFoundError:raise ValueError('NODE_IMAGE_INPUT_MISSING')from None
    except (KeyError,TypeError,ValueError,AttributeError,OSError,IndexError,UnicodeError):raise ValueError('NODE_IMAGE_PROOF_UNVERIFIED')from None
