"""Kernel metadata fixtures, never worker credential-access qualification."""
from copy import deepcopy
import hashlib,json
from types import SimpleNamespace

import pytest

from guard.runtime import kubernetes_process_mounts as m
from guard.runtime.kubernetes_process import PROFILE as PROCESS_PROFILE,SOURCE_SHA256 as PROCESS_SOURCE_SHA256

POLICY='/var/run/secrets/kubernetes.io/serviceaccount'
MOUNT=POLICY+'/token'
SPEC={'processes':[{'pid':12,'start_ticks':42,'mount_namespace_inode':7}],
      'declared_mount_paths':[MOUNT],'forbidden_mount_paths':[POLICY]}


def program(*,actual=True,changed=False,reused=False):
    namespace={'__name__':'test_readonly_source'}
    raw=m.source();assert hashlib.sha256(raw.encode()).hexdigest()==m.SOURCE_SHA256
    exec(compile(raw,'fixed-readonly-mount-source','exec'),namespace)
    namespace['os']=SimpleNamespace(stat=lambda path:SimpleNamespace(st_ino=7),
        path=SimpleNamespace(realpath=lambda path:path.replace('/var/run/','/run/')))
    calls=[]
    def points(pid):
        calls.append(pid)
        return ({'/',MOUNT.replace('/var/run/','/run/')} if actual else {'/'}),('b' if changed and len(calls)>1 else 'a')*64
    namespace['mountpoints']=points
    identities=[]
    def identity(pid):
        identities.append(pid)
        return (43 if reused and len(identities)>1 else 42,7)
    namespace['identity']=identity
    return namespace['capture'](deepcopy(SPEC))


def test_alias_is_bound_to_actual_kernel_mount_and_original_policy_path():
    value=program();m.decode(value,SPEC);row=value['rows'][0]
    assert row['stable'] and row['matches']==[{'policy_path':POLICY,'mount_path':MOUNT}]


def test_pod_declaration_or_root_filesystem_alone_is_not_mount_presence():
    value=program(actual=False);assert value['rows'][0]['stable']
    assert value['rows'][0]['matches']==[]


@pytest.mark.parametrize('damage',['changed','reused'])
def test_changed_pid_or_mount_cannot_keep_a_positive_match(damage):
    value=program(**{damage:True});m.decode(value,SPEC)
    assert not value['rows'][0]['stable'] and value['rows'][0]['matches']==[]


@pytest.mark.parametrize('damage',['pid','extra-policy','extra-mount','duplicate','unstable-positive','extra-key'])
def test_decoder_rejects_scope_changes_and_forged_positive_rows(damage):
    value=program();row=value['rows'][0]
    if damage=='pid':row['pid']+=1
    elif damage=='extra-policy':row['matches'][0]['policy_path']='/other'
    elif damage=='extra-mount':row['matches'][0]['mount_path']='/other'
    elif damage=='duplicate':row['matches']*=2
    elif damage=='unstable-positive':row['stable']=False
    else:row['secret']='NEVER_RETURN_PRIVATE'
    with pytest.raises(ValueError,match='PACKET_INVALID'):m.decode(value,SPEC)


class Transport:
    profile='kubernetes-owned-request-binding-http-v1';target_sha256='a'*64
    process_observation_origin='MEASURED'
    binding={'engine':{'namespace':'owned','pod':'engine','container':'engine'}}
    def __init__(self,damage=None):self.damage=damage;self.verifications=0;self.executions=0
    def _verify(self):
        self.verifications+=1
        if self.damage=='replacement' and self.verifications==2:raise ValueError('PRIVATE_NOT_RETURNED')
    def _call(self,argv,**kwargs):
        self.executions+=1
        assert argv[:8]==['--namespace=owned','exec','engine','-c','engine','--','python3','-I']
        assert kwargs=={'cap':262144,'budget':8}
        assert json.loads(argv[-1])==SPEC
        return program()


def record(t):
    return {'profile':PROCESS_PROFILE,'program_sha256':PROCESS_SOURCE_SHA256,'target_sha256':t.target_sha256,
        'data_origin':'EXAMPLE','available':True,'binding_verified_before_after':True,
        'binding_before':deepcopy(t.binding['engine']),'binding_after':deepcopy(t.binding['engine']),
        'mount_declarations_stable':True,'mount_declarations':[{'mount_path':MOUNT}],
        'mount_declarations_after':[{'mount_path':MOUNT}],
        'packet':{'capture_process_excluded':True,'capture_subject':{'identity_stable':True,'mount_namespace_inode':7},
            'processes':[{**SPEC['processes'][0],'pid_identity_stable':True}]}}


def test_live_transport_keeps_example_provenance_and_no_whole_worker_claim():
    t=Transport();value=m.capture(t,record(t),[POLICY])
    assert value['available'] and value['violations']==[POLICY]
    assert value['data_origin']=='EXAMPLE' and not value['whole_worker_coverage_verified']
    assert t.verifications==2 and t.executions==1


@pytest.mark.parametrize('damage',['target','source','binding','mounts','origin','replacement'])
def test_foreign_or_changed_capture_cannot_expose_a_positive_mount_fact(damage):
    t=Transport(damage);r=record(t)
    if damage=='target':r['target_sha256']='b'*64
    elif damage=='source':r['program_sha256']='b'*64
    elif damage=='binding':r['binding_before']['pod']='other'
    elif damage=='mounts':r['mount_declarations_after']=[]
    elif damage=='origin':r['data_origin']='UNVERIFIED_PROVENANCE'
    value=m.capture(t,r,[POLICY])
    assert not value['available'] and not value['violations']
    assert 'PRIVATE_NOT_RETURNED' not in json.dumps(value)
    if damage!='replacement':assert t.executions==0


def test_credential_consumer_projects_positive_mount_ban_without_claiming_worker_access(tmp_path):
    from guard.executors.credential_exposure import collect_kubernetes_credential_exposure
    from tests.test_kubernetes_process_consumers import context,CREDENTIAL_VALUES
    from tests.builders import baseline_pack
    ctx,world=context(tmp_path)
    ctx.check=baseline_pack().checks['PLT-04']
    ctx.policy_values={**CREDENTIAL_VALUES,'forbidden_mount_paths':[POLICY]}
    original=world.runner
    def runner(argv,**kwargs):
        if 'exec' in argv and len(argv)>2 and argv[-2]==m.source():
            spec=json.loads(argv[-1])
            value={'schema':m.SCHEMA,'scope':'selected-container-mount-metadata-only',
                'rows':[{**row,'stable':True,'matches':[{'policy_path':POLICY,'mount_path':MOUNT}],
                         'mountinfo_sha256':'a'*64,'diagnostic':None} for row in spec['processes']]}
            return {'returncode':0,'stdout_bytes':json.dumps(value).encode(),
                'capture':{'complete':True,'bounded_at_source':True,'timed_out':False,'truncated':False}}
        result=original(argv,**kwargs)
        if 'get' in argv:
            value=json.loads(result['stdout_bytes'])
            if value.get('kind')=='Pod':
                value['spec']['containers'][0]['volumeMounts']=[{'name':'credential-volume','mountPath':MOUNT}]
                result['stdout_bytes']=json.dumps(value).encode()
        return result
    ctx.cprobe.runner=runner
    observations,normal,challenges,summary=collect_kubernetes_credential_exposure(ctx)
    assert observations['forbidden_mount_violations']=={'value':[POLICY],'collected':True}
    assert observations['process_scan_complete']=={'value':False,'collected':True}
    assert observations['worker_env_names']=={'value':None,'collected':False}
    assert not summary['worker_subject_verified'] and not summary['candidate_access_verified']
    assert not summary['actual_process_mount_observation']['whole_worker_coverage_verified']
