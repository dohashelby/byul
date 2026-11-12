"""Assemble the existing callback HTTP target from supplied actual role inputs."""
import argparse,hashlib,json,os,stat
from pathlib import Path

from guard.runtime.kubernetes_binding import validate_container_binding
BINDING_SOURCE_COMMIT = 'b85dac21bfe380eaa7f06a7fe2b451a2e168daa2'
BINDING_SOURCE_FILE_SHA256 = '5d541fabeb170309461b7fae856077423a9d86898360b720456569832e63b8aa'
BINDING_SOURCE_GIT_BLOB = '4c96dde3d0feb58c6602e3f72a2437df50b95730'
BINDING_FUNCTION_SHA256 = '9d252bb5d961a6292b8fa30f5e67551d4a9f9fd745e7666ff6370231e5400f59'

def _binding(pod, namespace, container, role_label=None):
    meta, spec, status = pod['metadata'], pod['spec'], pod['status']
    state, = (s for s in status['containerStatuses'] if s['name'] == container)
    image, = (c['image'] for c in spec['containers'] if c['name'] == container)
    if role_label is not None and meta['labels'].get('app') != role_label:
        raise ValueError('RUN_INPUTS_POD_ROLE_LABEL_MISMATCH')
    item = {'namespace': meta['namespace'], 'namespace_uid': namespace['metadata']['uid'], 'pod': meta['name'],
        'pod_uid': meta['uid'], 'container': container, 'container_id': state['containerID'], 'image': image,
        'image_id': state['imageID'], 'restart_count': state['restartCount'], 'node': spec['nodeName'],
        'pod_ip': status['podIP'], 'started_at': state['state']['running']['startedAt']}
    validate_container_binding(item)
    return item

def read(path,expected):
    p=Path(path).absolute()
    if any(x.is_symlink() for x in (p,*p.parents)):raise ValueError('REQ_BIND_INPUT_LINK')
    fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,'rb') as f:
        before=os.fstat(f.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_uid!=1000 or before.st_nlink!=1
                or stat.S_IMODE(before.st_mode)!=0o600 or before.st_size>2*1024**2):
            raise ValueError('REQ_BIND_OWNED_INPUT_REQUIRED')
        raw=f.read(2*1024**2+1);after=os.fstat(f.fileno());current=p.lstat()
    key=lambda s:(s.st_dev,s.st_ino,s.st_uid,s.st_gid,s.st_mode,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
    if key(before)!=key(after) or key(before)!=key(current) or hashlib.sha256(raw).hexdigest()!=expected:
        raise ValueError('REQ_BIND_INPUT_CHANGED')
    return json.loads(raw)

def build(env,namespace,engine_pod,probe_pod,mapping,context,run_id):
    from guard.collectors.model_mapping import validate_model_mapping
    from guard.runtime.kubernetes_binding import verify_owned_pod
    from guard.runtime.kubernetes_http import KubernetesHTTP,ENGINE_CALLBACK_PROFILE
    if (namespace.get('kind')!='Namespace' or namespace['metadata'].get('deletionTimestamp')
            or namespace['metadata'].get('labels',{}).get('guard.run')!=run_id
            or namespace['metadata'].get('labels',{}).get('byul.lab/owned')!='true'):
        raise ValueError('REQ_BIND_CURRENT_OWNED_LAB_REQUIRED')
    engine=_binding(engine_pod,namespace,'engine');probe=_binding(probe_pod,namespace,'probe')
    for pod,bound in ((engine_pod,engine),(probe_pod,probe)):
        verify_owned_pod(pod,namespace['metadata'],bound,run_id)
    if engine['pod_uid']==probe['pod_uid'] or engine['container_id']==probe['container_id']:
        raise ValueError('REQ_BIND_DISTINCT_ENGINE_PROBE_REQUIRED')
    mapping=validate_model_mapping(env,mapping)
    target={'schema':'guard-kubernetes-request-binding-target/1','lab':True,'topology':'kubernetes-owned-direct',
        'engine_kind':'vllm','origins':{'trusted':{'network':'kubernetes-probe'}},
        'engine':{'trusted_url':f"http://{engine['pod_ip']}:8000",'image_digest':env.engine.image_digest},
        'kubernetes':{'context':context,'run_id':run_id,'engine':engine,'probe':probe},
        'environment_ref':env.ref().model_dump(mode='json'),'model_mapping':mapping}
    def no_exec(*args,**kwargs):raise AssertionError('REQ_BIND_NO_RUNTIME_DISPATCH')
    KubernetesHTTP(target,env,duration=1,runner=no_exec,profile=ENGINE_CALLBACK_PROFILE)
    return target

def main():
    from guard.models import Environment,canonical_json
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('environment','namespace','engine-pod','probe-pod','model-mapping'):
        p.add_argument('--'+name,required=True);p.add_argument('--'+name+'-sha256',required=True)
    p.add_argument('--context',required=True);p.add_argument('--run-id',required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    try:
        data={name:read(getattr(a,name),getattr(a,name+'_sha256')) for name in ('environment','namespace','engine_pod','probe_pod','model_mapping')}
        env=Environment.model_validate(data['environment'])
        target=build(env,data['namespace'],data['engine_pod'],data['probe_pod'],data['model_mapping'],a.context,a.run_id)
        raw=canonical_json(target)
        fd=os.open(a.output,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
    except (OSError,ValueError,TypeError,KeyError,AssertionError):
        print('REQ_ACTUAL_INPUT_BINDING_FAILED');raise SystemExit(2) from None
    print(json.dumps({'target_sha256':hashlib.sha256(raw).hexdigest(),'input_only':True,'runtime_dispatch':False,'GPU_MEASURED':False}))

if __name__=='__main__':main()
