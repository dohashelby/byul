"""EXAMPLE task proofs and owned stdlib children; never GPU measurements."""
from copy import deepcopy
from hashlib import sha256
import json,subprocess,sys,time
from pathlib import Path
import pytest
from guard.executors import node_gpu_process as collector,node_image_proof
from guard.runtime import gpu_process_source
from guard.adapters import gpu_process_probe as probe
from tests.test_gpu_process_probe import packet,example_scope


def setup():
    binding={'lab':True,'engine':{'pod_uid':'EXAMPLE-pod','container_id':'containerd://'+'a'*64,
        'node':'EXAMPLE-node','namespace':'EXAMPLE','pod':'engine','container':'engine',
        'image':'EXAMPLE-image','image_id':'EXAMPLE-image'}}
    receipt={'schema':'guard-node-image-proof/1','profile':node_image_proof.PROFILE,
        'task':{'pid':101,'start_ticks':100,'pid_namespace':11},'observed_monotonic_ns':time.monotonic_ns(),
        'node':{'node':'EXAMPLE-node','node_uid':'EXAMPLE-uid','boot_id_sha256':'a'*64},'data_origin':'EXAMPLE'}
    proof=node_image_proof.NodeImageProof(binding['engine'],receipt,_key=node_image_proof._PROOF_KEY)
    return binding,proof


def test_closed_live_program_and_scope_metadata_preserve_example_origin_and_no_registration():
    binding,proof=setup();calls=[]
    def run(argv,**kwargs):
        calls.append(argv);assert argv[:5]==[sys.executable,'-I','-S','-B','-c']
        wire=json.loads(kwargs['input_bytes']);assert wire['selection']==example_scope()
        assert wire['max_seconds']==10 and kwargs['max_stdout_bytes']==probe.MAX_OUTPUT
        p=packet();now=time.monotonic_ns();wall=time.time_ns()
        p.update(started_ns=now,finished_ns=now,started_wall_ns=wall,finished_wall_ns=wall)
        return {'returncode':0,'stdout_bytes':probe.canonical(p),'stderr_bytes':b'','capture':{'complete':True}}
    result=collector.collect(binding,proof,example_scope()['owners'],runner=run)
    assert result.matches(binding)and len(calls)==1
    other=deepcopy(binding);other['engine']['container_id']='containerd://'+'b'*64
    assert not result.matches(other)
    public=result.public_receipt();assert public['data_origin']=='EXAMPLE'
    assert public['actual_gpu_source_qualified']is public['producer_registered']is public['whole_gpu_release_verified']is False
    assert public['whole_group_recovery_verified']is public['engine_launched_by_collector']is False
    assert public['packet_sha256']==sha256(probe.canonical(result.packet())).hexdigest()
    changed=result.packet();changed['host_inventory_complete']=False
    assert result.packet()['host_inventory_complete']is True
    with pytest.raises(ValueError,match='GPU_PROCESS_COLLECTED_SNAPSHOT_REQUIRED'):
        collector.NodeGpuProcessSnapshot({}, {}, {})


@pytest.mark.parametrize('damage',['operator-dict','foreign-cid','expired','future','other-boot'])
def test_task_identity_or_lifetime_refused_before_child_launch(damage):
    binding,proof=setup();owners=example_scope()['owners'];calls=[]
    if damage=='operator-dict':proof={'live_root_mount_verified':True}
    elif damage=='foreign-cid':binding['engine']['container_id']='containerd://'+'b'*64
    elif damage=='expired':proof._receipt['observed_monotonic_ns']-=31_000_000_000
    elif damage=='future':proof._receipt['observed_monotonic_ns']+=31_000_000_000
    else:owners[0]['context']['boot_sha256']='c'*64
    def forbidden(*args,**kwargs):calls.append(True);raise AssertionError('source checks precede launch')
    with pytest.raises(ValueError):collector.collect(binding,proof,owners,runner=forbidden)
    assert not calls


@pytest.mark.parametrize('damage',['bad-clock','stderr','incomplete','claim','wrong-selection'])
def test_incomplete_tampered_or_foreign_packet_never_becomes_a_snapshot(damage):
    binding,proof=setup()
    def run(*args,**kwargs):
        p=packet();now=time.monotonic_ns();wall=time.time_ns()
        p.update(started_ns=now,finished_ns=now,started_wall_ns=wall,finished_wall_ns=wall)
        if damage=='bad-clock':p['started_ns']=p['finished_ns']=0
        elif damage=='claim':p['gpu_release_verified']=True
        elif damage=='wrong-selection':p['selection_sha256']='0'*64
        return {'returncode':0,'stdout_bytes':probe.canonical(p),'stderr_bytes':b'EXAMPLE-private'if damage=='stderr'else b'',
            'capture':{'complete':damage!='incomplete'}}
    with pytest.raises(ValueError):collector.collect(binding,proof,example_scope()['owners'],runner=run)


def test_standalone_program_rejects_a_subset_before_any_gpu_command():
    program=gpu_process_source.source()
    child=subprocess.run([sys.executable,'-I','-S','-c',program],capture_output=True,
        input=json.dumps({'selection':example_scope(),'max_seconds':1,'process_ids':[1]}).encode())
    assert child.returncode!=0 and b'GPU_PROCESS_INPUT_INVALID'in child.stderr
    assert not child.stdout


def test_standalone_source_requires_every_exact_reviewed_dependency(monkeypatch):
    read=Path.read_bytes
    monkeypatch.setattr(Path,'read_bytes',lambda p:read(p)+(b'\n'if p.name=='gpu_process_probe.py'else b''))
    with pytest.raises(ValueError,match='GPU_PROCESS_SOURCE_UNREVIEWED'):gpu_process_source.source()


def test_new_node_collector_does_not_change_the_registered_input_sdk():
    from guard.collectors.native_input import producer
    from guard.collectors.native_input_qualification import status
    data=json.loads((Path(__file__).parents[1]/'src/guard/adapters/assets/native_input_qualifications.json').read_bytes())
    row,=[r for r in data['qualifications']if r['producer']==producer()]
    assert producer()['source_sha256']=='5c9e6a5c956b8f2ec0873e71941776eb7027b16b4fd185c4606dd2125938b5d6'
    assert status(row['engine'],row['version'],row['image'])['registered']is True


@pytest.mark.parametrize('mode',['normal','owner-replaced','node-replaced','final-read-error','capture-error'])
def test_reader_rechecks_node_and_owner_without_erasing_an_earlier_gpu_packet(monkeypatch,mode):
    binding,proof=setup()
    class Reader:
        lab=True;injected=True;deadline=time.monotonic()+120
        def __init__(self):self.binding=binding;self.reads=0;self.owners=0;self.runner=lambda *a,**k:None
        def _remaining(self,deadline):return deadline-time.monotonic()
        def _owner(self,timeout):
            self.owners+=1
            return {'container_id':'other'if self.owners==2 and mode=='owner-replaced'else'EXAMPLE'}
        def _request(self,args,**kwargs):
            assert args==['get','node',binding['engine']['node'],'-o','json']
            self.reads+=1
            if self.reads==2 and mode=='final-read-error':raise OSError('PRIVATE_READ_FAILURE')
            return b'{}'
    monkeypatch.setattr(node_image_proof,'collect_local_node_image_proof',lambda *a,**k:proof)
    monkeypatch.setattr(node_image_proof,'kernel_identity',lambda:{})
    def validate(*args):
        if mode=='node-replaced':raise ValueError('PRIVATE_NODE_FAILURE')
        return proof.public_receipt()['node']
    monkeypatch.setattr(node_image_proof,'validate_node',validate)
    def capture(*args,**kwargs):
        if mode=='capture-error':raise OSError('PRIVATE_CAPTURE_FAILURE')
        return collector.NodeGpuProcessSnapshot(binding['engine'],packet(),{'selection':example_scope()},_key=collector._KEY)
    monkeypatch.setattr(collector,'collect',capture)
    result=collector.collect_for_reader(Reader(),example_scope()['owners'],containerd_address='/run/k3s/containerd/containerd.sock')
    assert result['collected']is result['owner_binding_verified']is result['clock_window_verified']is(mode=='normal')
    assert (result['packet']is not None)is(mode!='capture-error')
    assert b'PRIVATE'not in probe.canonical(result)
