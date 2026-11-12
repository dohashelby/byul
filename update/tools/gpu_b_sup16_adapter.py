"""Minimal SUP16 open/close connection to original S2 operating APIs.

No main, AWS call, model preparation, common validator or product factory.
S2 bind_context supplies the original450467 APIs with CURRENT role globals,
source/phase state, latest verified Pod callback and new archive alias. This
module executes those APIs only when S2 runs its next fixed operating packet.
"""
import ast
import base64
import datetime
import hashlib
import json
from pathlib import Path
import re
import time

OLD_SHA = '45046718557d1d2b5a77f210f9a77c23a7d965af92c65ca1a1354b7e8b212fe4'
IMAGE = 'vllm/vllm-openai@sha256:a4a4c0437bf7240089da5f08aa370c4aee17ae5290f7a3b468825ee26c4c3a6b'
SOURCE_FILES_SHA = 'c63bb8c102e8515cd7e45ada9fe3ea390a722cde157988c5b6bf9ac48ca71c30'
PROFILE_SOURCE = "import guard,json,pathlib,sys;from guard.adapters.vllm_lifecycle_profiles import source_hashes;assert pathlib.Path(guard.__file__).resolve().is_relative_to(pathlib.Path(sys.argv[1])/'update/src/guard');print(json.dumps(source_hashes('vllm-native-lifecycle-metadata/17'),sort_keys=True))"

# Current-source owner process creates the original sealed ENV/target only
# AFTER S2 supplied real Namespace/Pod/container/node/image/probe observations.
# No sys.path insertion, invented Pod IDs or future role session is used.
MAKE_INPUTS = r"""import os,sys,json,pathlib,hashlib
import guard
from guard.models import Environment,new_id
p=pathlib.Path(sys.argv[1]);m=json.loads(sys.argv[2]);s=m['inputs']
assert os.getuid()==os.geteuid()==1000 and not p.exists()
assert pathlib.Path(guard.__file__).resolve().is_relative_to(pathlib.Path(s['source_root'])/'update/src/guard')
assert not any(x.is_symlink() for x in [p,*p.parents])
os.umask(0o077);p.mkdir(mode=0o700)
env=Environment(id=new_id('ENV'),data_origin='EXAMPLE',engine={'name':'vllm','version':m['probe']['version'],'image_digest':m['image'],'source_commit_status':'UNVERIFIED'},runtime={'nodes':1,'tp_groups':1,'gpu_count':1,'gpu_model':'NVIDIA L4','platform':'actual B owned K3s TP1 native17 role LAB diagnostic'},model=m['model']).seal()
target={'schema':'guard-kubernetes-lifecycle-target/1','run_id':m['run'],'environment_ref':env.ref().model_dump(mode='json'),'lab':True,'kubernetes':{'context':m['context'],'engine':m['engine']}}
def save(name,value):
 raw=json.dumps(value,sort_keys=True).encode();q=p/name
 fd=os.open(q,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
 return {'path':str(q),'file_sha256':hashlib.sha256(raw).hexdigest()}
s['environment']=save('ENV.json',env.to_dict());s['target']=save('target.json',target)
s['pod']=save('pod.json',m['pod']);s['namespace']=save('namespace.json',m['namespace'])
print(json.dumps(save('inputs.json',s),sort_keys=True))
"""


def require(ok, code):
    if not ok:
        raise ValueError(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def original_literal(raw, function, variable):
    """Read exact original DATA strings, never execute old setup/install/main."""
    require(sha(raw) == OLD_SHA, 'SUP16_ADAPTER_OLD_DATA_CHANGED')
    body, = [n for n in ast.parse(raw).body if isinstance(n, ast.FunctionDef) and n.name == function]
    values = [n.value for n in ast.walk(body) if isinstance(n, ast.Assign)
              and any(isinstance(t, ast.Name) and t.id == variable for t in n.targets)]
    require(len(values) == 1, 'SUP16_ADAPTER_ORIGINAL_LITERAL_REQUIRED')
    result = ast.literal_eval(values[0])
    require(type(result) is str, 'SUP16_ADAPTER_ORIGINAL_STRING_REQUIRED')
    return result


class SUP16RoleAdapter:
    """S2's bind_context(cfg) returns original APIs bound to this role only.

    Required APIs: command/owner/kube/obj/put_new_document/write/own_ns,
    verify_preparation/exec_preparation, read_regular/write_new/remaining,
    cleanup_context/quiesce/archive. Their common protection bodies are not
    recreated here. cleanup_context enters ORIGINAL independent cleanup state;
    archive retains original512MiB/secret/CRC/SHA guards with cfg's NEW alias.
    shared/roles are S2 finalconfig, not fixtures or automatically adopted pins.
    """
    def __init__(self, *, bind_context, shared, roles):
        self.bind_context, self.shared, self.roles = bind_context, shared, roles
        self.ops, self.cfg, self.open_partial = None, None, None

    def __getattr__(self, name):
        # run_roles consumes the current role's original controller callbacks.
        return getattr(self.ops, name)

    def preparation_argv(self, argv, *, stdin):
        return list(self.ops.K) + ['-n', self.cfg['namespace'], 'exec',
            *(['-i'] if stdin else []), self.cfg['pod'], '--container=preparation', '--', *argv]

    def _owner(self, label, argv, timeout=45):
        return self.ops.owner(label, argv, timeout=min(timeout, self._open_left()))

    def _open_left(self):
        left = self.ops.remaining()
        if self.open_partial['asset_deadline_monotonic'] is not None:
            left = min(left, self.open_partial['asset_deadline_monotonic'] - time.monotonic())
        require(left > 0, 'SUP16_ADAPTER_ORIGINAL_ASSET90_EXPIRED')
        return left

    def _prep(self, label, argv, timeout=30):
        return self.ops.exec_preparation(label, argv, timeout=min(timeout, self._open_left()))

    def open_role(self, phase, previous):
        cfg, shared = self.roles[phase], self.shared
        self.cfg = cfg
        self.ops = self.bind_context(cfg)
        self.open_partial = {'phase': phase, 'created_namespace': None, 'created_pod': None,
                             'asset_deadline_monotonic': None, 'cleanup': None, 'archive': None}
        try:
            require(cfg['phase'] == phase and cfg['runtime_class'] == 'nvidia', 'SUP16_ADAPTER_ACTUAL_ROLE_CONFIGURATION')
            require(not Path(cfg['runtime']).exists() and not Path(cfg['join_root']).exists(), 'SUP16_ADAPTER_NEW_ROLE_PATHS')
            require(not (Path(cfg['outer_root'])/'created-namespace.json').exists()
                    and not (Path(cfg['outer_root'])/('created-'+cfg['pod']+'.json')).exists(), 'SUP16_ADAPTER_PRESERVE_OLD_GENERATIONS')
            # These are actual CURRENT S2 verification receipts. Hash-copying
            # them is provenance, not a replacement source/model verifier.
            for name in ('source_current_receipt', 'model_current_receipt'):
                pointer = cfg[name]
                body = self.ops.read_regular(Path(pointer['path']), pointer['owner'], 3*1024**2)
                require(sha(body) == pointer['file_sha256'], 'SUP16_ADAPTER_CURRENT_PROOF_CHANGED')
                self.ops.write(Path(cfg['outer_root']) / (name + '.json'), body)
            render = cfg['render']
            argv = [shared['python'], '-I', '-B', shared['resolver'], 'render', '--phase', phase,
                    '--template', render['template'], '--template-sha256', render['template_sha256'],
                    '--init-asset', render['init_asset'], '--init-asset-sha256', render['init_asset_sha256'],
                    '--init-code', render['init_code'], '--init-code-sha256', render['init_code_sha256'],
                    '--mount-pin-sha256', render['mount_pin_sha256'], '--uid', '1000', '--gid', '1000',
                    '--output', render['output']]
            self._owner('sup16-original-render-' + phase, argv)
            manifest = json.loads(self.ops.read_regular(Path(render['output']) / 'template.json', 1000, 2*1024**2))
            pod, = [x for x in manifest['items'] if x['kind'] == 'Pod']
            cm, = [x for x in manifest['items'] if x['kind'] == 'ConfigMap']
            require(pod['metadata']['name'] == cfg['pod'], 'SUP16_ADAPTER_ORIGINAL_POD_NAME')
            for doc in (pod, cm):
                doc['metadata']['namespace'] = cfg['namespace']
                doc['metadata'].setdefault('labels', {})['guard.run'] = cfg['run']
            pod['spec']['nodeName'] = cfg['node']; pod['spec']['runtimeClassName'] = cfg['runtime_class']
            model, = [v for v in pod['spec']['volumes'] if v['name'] == 'model']
            model['hostPath']['path'] = cfg['model_root']
            # Same original450467 operational shm overlay, not a new mount rule.
            pod['spec']['volumes'].append({'name':'runtime-shm','emptyDir':{'medium':'Memory','sizeLimit':'2Gi'}})
            engine, = [c for c in pod['spec']['containers'] if c['name'] == 'engine']
            engine['volumeMounts'].append({'name':'runtime-shm','mountPath':'/dev/shm'})
            self.ops.write_new(Path(cfg['code']) / 'deployment-candidate.json', (json.dumps(manifest,sort_keys=True)+'\n').encode(), 0)
            namespace = self.ops.put_new_document('namespace', {'apiVersion':'v1','kind':'Namespace',
                'metadata':{'name':cfg['namespace'],'labels':{'guard.run':cfg['run'],'byul.lab/owned':'true'}}})
            self.open_partial['created_namespace'] = namespace
            self.ops.write(Path(cfg['outer_root']) / 'created-namespace.json', namespace)
            self.ops.put_new_document('prepare-code-configmap', cm)
            created = self.ops.put_new_document('role-pod', pod)
            self.open_partial['created_pod'] = created
            self.ops.write(Path(cfg['outer_root']) / ('created-' + cfg['pod'] + '.json'), created)
            until = time.monotonic() + 90  # original prepublication startup90
            while True:
                current = self.ops.verify_preparation('sup16-role-containers-startup')
                statuses = current.get('status', {}).get('containerStatuses', [])
                running = next((s for s in statuses if s['name']=='engine' and 'running' in s.get('state',{})), None)
                if self.open_partial['asset_deadline_monotonic'] is None and running is not None:
                    start = running['state']['running']['startedAt']
                    end = datetime.datetime.fromisoformat(start.replace('Z','+00:00')) + datetime.timedelta(seconds=90)
                    left = (end - datetime.datetime.now(datetime.timezone.utc)).total_seconds()
                    require(0 < left <= 90, 'ORIGINAL_ENGINE_ASSET90_ALREADY_EXHAUSTED')
                    self.open_partial['asset_deadline_monotonic'] = time.monotonic() + left
                    self.ops.write(Path(cfg['outer_root']) / 'actual-engine-publication-deadline.json',
                        {'engine_startedAt':start,'remaining_seconds_at_observation':left,'original_asset_wait_seconds':90,'launcher_timer_reset':False})
                if len(statuses)==2 and all('running' in s.get('state',{}) and s.get('restartCount')==0 for s in statuses):
                    break
                require(time.monotonic() < until and self.ops.remaining() > 0, 'PREPUBLICATION_ASSET_WAIT90')
                time.sleep(min(1, max(0,until-time.monotonic()), max(0,self.ops.remaining())))
            self._open_left()
            init = current.get('status',{}).get('initContainerStatuses',[])
            require(len(init)==1 and init[0]['state'].get('terminated',{}).get('exitCode')==0 and init[0].get('restartCount')==0, 'ACTUAL_PRIVATE_ROOT_INIT_REQUIRED')
            init_raw = self.ops.kube('actual-private-root-init-log','-n',cfg['namespace'],'logs',cfg['pod'],'--container='+init[0]['name'],timeout=min(45,self._open_left()))
            old = self.ops.read_regular(Path(shared['ready_controller']),1000,2*1024**2)
            root_probe = original_literal(old, 'native_observe', 'root_probe')
            root_identity = json.loads(self._prep('actual-private-data-root-fd-identity',['python3','-I','-S','-B','-c',root_probe]))
            self.ops.write(Path(cfg['outer_root'])/'actual-private-root-init-and-mount.json',{'init_status':init,'init_stdout_sha256':sha(init_raw),'mounted_data_root':root_identity})
            probe = json.loads(self._prep('actual-installed-image-support',['python3','-I','-B','/guard-prepare/runtime_probe.py']))
            require(probe['uid']==1000 and probe['support_matches_existing_native17'] is True, 'ACTUAL_NATIVE17_SUPPORT_MISMATCH')
            expected = json.loads(self._owner('current-source-native17-profile',[shared['python'],'-I','-B','-c',PROFILE_SOURCE,shared['source_root']]))
            support = cfg['source_files_nosite']
            source = self.ops.read_regular(Path(support['path']),support['owner'],2*1024**2)
            require(sha(source)==support['file_sha256']==SOURCE_FILES_SHA,'SUP16_ADAPTER_SUPPORT_TOOL_CHANGED')
            actual = json.loads(self._prep('all-original-native17-source-pins',['python3','-I','-S','-B','-c',source.decode(),json.dumps(sorted(expected))]))
            require(actual==expected,'UNSUPPORTED_SOURCE_NO_HASH_APPROVAL')
            self.ops.write(Path(cfg['outer_root'])/'original-native17-source-pins.json',{'profile':'vllm-native-lifecycle-metadata/17','actual':actual,'expected':expected,'new_hash_approval':False})
            current = self.ops.verify_preparation('actual-binding-pod')
            status, = [s for s in current['status']['containerStatuses'] if s['name']=='engine']
            require(status['imageID'].endswith('@'+IMAGE.split('@')[1]) and status['restartCount']==0,'PINNED_NEW_ENGINE_GENERATION_REQUIRED')
            ns = self.ops.own_ns('actual-binding-namespace')
            node = self.ops.obj('actual-binding-node','get','node',cfg['node'])
            gpu = self.ops.command('actual-binding-gpu',['nvidia-smi','--query-gpu=name,uuid','--format=csv,noheader'],timeout=min(45,self._open_left()))
            boot = self.ops.command('actual-binding-boot',['cat','/proc/sys/kernel/random/boot_id'],timeout=min(45,self._open_left()))
            context = self.ops.kube('actual-binding-context','config','current-context',timeout=min(45,self._open_left())).decode().strip()
            identity = {'namespace':ns,'pod':current,'node':node,'GPU':gpu.decode(),'node_boot_id':boot.decode().strip(),'engine_container_status':status}
            self.ops.write(Path(cfg['outer_root'])/'actual-generation-before-publish.json',identity)
            engine = {'namespace':cfg['namespace'],'namespace_uid':ns['metadata']['uid'],'pod':cfg['pod'],
                'pod_uid':current['metadata']['uid'],'container':'engine','container_id':status['containerID'],
                'image':IMAGE,'image_id':status['imageID'],'restart_count':status['restartCount'],'node':cfg['node'],
                'pod_ip':current['status']['podIP'],'started_at':status['state']['running']['startedAt']}
            inputs = {'schema':'byul-sup16-role-command-inputs/1','phase':phase,'source_root':shared['source_root'],
                'source_commit':shared['source_commit'],'source_manifest_sha256':shared['source_manifest_sha256'],
                'context':context,'model_manifest':shared['model_manifest'],'previous_roles':list(previous),
                'static_preparation':shared['static_preparation'],'staging_receipt_sha256':cfg['staging_receipt_sha256'],
                'role_output':str(Path(cfg['runtime'])/'role'),'capture_output':str(Path(cfg['runtime'])/'capture'),
                'capture_helper':{'path':shared['capture_helper'],'file_sha256':'bb9a2839b2e2166af3f4d2309bbe5afe260a2eee2f49d05434913d2084eb4cb7'},'kubectl_binary':shared['kubectl_binary']}
            metadata = dict(inputs=inputs,model=shared['model'],probe=probe,image=IMAGE,run=cfg['run'],
                            context=context,engine=engine,pod=current,namespace=ns)
            pointer = json.loads(self._owner('write-owner-actual-role-inputs',[shared['python'],'-I','-B','-c',MAKE_INPUTS,cfg['runtime'],json.dumps(metadata)]))
            body = self.ops.read_regular(Path(pointer['path']),1000,3*1024**2)
            require(pointer['path']==str(Path(cfg['runtime'])/'inputs.json') and sha(body)==pointer['file_sha256'],'SUP16_ADAPTER_ACTUAL_INPUT_POINTER')
            self._open_left()
            return {'root':cfg['join_root'],'runtime':cfg['runtime'],'inputs':pointer,'python':shared['python'],
                    'resolver':shared['resolver'],'bridge':shared['bridge'],'ready_controller':shared['ready_controller'],
                    'asset_deadline_monotonic':self.open_partial['asset_deadline_monotonic']}
        except BaseException as first:
            # No returned frame exists. Still preserve actual creation replies
            # and execute original independent owned cleanup/archive APIs.
            try:
                self._finish(first)
            except BaseException as later:
                if later is not first:
                    first.add_note('SUP16_OPEN_CLEANUP_ERROR:'+type(later).__name__+':'+str(later))
            first.sup16_open_partial = self.open_partial
            raise

    def _preserve(self):
        cfg, ops = self.cfg, self.ops
        runtime = Path(cfg['runtime'])
        if runtime.exists():
            for path in sorted(runtime.rglob('*')):
                require(not path.is_symlink(),'NO_PRESERVATION_INPUT_SYMLINK')
                if path.is_file():
                    ops.write(Path(cfg['outer_root'])/'preservation-runtime-snapshot'/path.relative_to(runtime),ops.read_regular(path,1000,64*1024**2))
        if self.open_partial['created_namespace'] is None:
            return
        ops.own_ns('preserve-namespace')
        pods = ops.obj('preserve-all-owned-pods','-n',cfg['namespace'],'get','pods')
        ops.write(Path(cfg['outer_root'])/'preserve-pods.json',pods)
        for pod in pods['items']:
            require(pod['metadata']['labels'].get('guard.run')==cfg['run'],'ONLY_OWN_RUN_LOG_EXPORT')
            for container in pod['spec'].get('initContainers',[])+pod['spec']['containers']:
                ops.kube('preserve-'+pod['metadata']['name']+'-'+container['name'],'-n',cfg['namespace'],'logs',pod['metadata']['name'],'--container='+container['name'],allowed=(0,1))
        receipt = runtime/'role/role-preparation.json'
        if receipt.exists() and any(p['metadata']['name']==cfg['pod'] and any(s['name']=='preparation' and 'running' in s.get('state',{}) for s in p.get('status',{}).get('containerStatuses',[])) for p in pods['items']):
            session = json.loads(ops.read_regular(receipt,1000,2*1024**2))['session']
            require(re.fullmatch('[a-f0-9]{32}',session),'EXACT_PRESERVED_SESSION')
            old = ops.read_regular(Path(self.shared['ready_controller']),1000,2*1024**2)
            source = original_literal(old,'preserve','source')
            bundle = json.loads(ops.exec_preparation('preserve-current-native-originals',['python3','-I','-S','-B','-c',source,session],timeout=45))
            require(bundle['session']==session,'PRESERVED_SESSION_MATCH')
            for row in bundle['files']:
                body=base64.b64decode(row['raw_b64'],validate=True)
                require(len(body)==row['bytes'] and sha(body)==row['sha256'],'PRESERVED_NATIVE_EXACT_BYTES')
                ops.write(Path(cfg['outer_root'])/'preservation-native-originals'/row['name'],body)

    def _finish(self, original):
        errors = []
        try:
            self.ops.cleanup_context()  # ORIGINAL state/budget; never Pod90 reset.
        except BaseException as error:
            if original is not None:
                original.add_note('SUP16_CLEANUP_CONTEXT_ERROR:'+type(error).__name__+':'+str(error))
                original.sup16_open_partial = self.open_partial
                raise original
            error.sup16_open_partial = self.open_partial
            raise
        for label, action in (('preserve',self._preserve),('quiesce',self.ops.quiesce),('archive',self.ops.archive)):
            try:
                value = action()
                if label=='quiesce' and self.open_partial['created_namespace'] is not None:
                    path=Path(self.cfg['outer_root'])/'cleanup-complete.json'
                    self.open_partial['cleanup']=json.loads(self.ops.read_regular(path,0,2*1024**2))
                if label=='archive':
                    self.open_partial['archive']=value
            except BaseException as error:
                errors.append(error)
        if errors:
            first = original if original is not None else errors[0]
            for error in errors:
                if error is not first:
                    first.add_note('SUP16_ROLE_CLOSE_ERROR:'+type(error).__name__+':'+str(error))
            first.sup16_open_partial = self.open_partial
            raise first
        return self.open_partial['cleanup']

    def close_role(self, frame, partial, error):
        return self._finish(error)
