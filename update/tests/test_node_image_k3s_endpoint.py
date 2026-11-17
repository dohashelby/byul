"""One explicit local k3s socket; no arbitrary endpoints or daemon fallback."""
import pytest
from guard.executors import node_image_proof as m
from tests.test_node_image_proof import World,NODE,KERNEL

ADDRESS='/run/k3s/containerd/containerd.sock'

def test_k3s_socket_is_used_for_both_containerd_metadata_and_readonly_mount_rpc(monkeypatch):
    monkeypatch.setattr(m,'verify_grpcurl_binary',lambda:None)
    world=World();calls=[]
    def runner(argv,**kwargs):
        calls.append(argv)
        if argv[:3]==['sudo','-n','ctr']:
            assert argv[3:7]==['--address',ADDRESS,'-n','k8s.io']
            return world.run([*argv[:3],*argv[5:]],**kwargs)
        assert argv[:3]==['sudo','-n',m.GRPCURL_BINARY]
        assert argv[-2]=='unix://'+ADDRESS
        return world.run([*argv[:-2],'unix:///run/containerd/containerd.sock',argv[-1]],**kwargs)
    proof=m.collect_local_node_image_proof({'engine':world.engine},NODE,runner=runner,
        kernel=KERNEL,proc_reader=world.process,containerd_address=ADDRESS)
    assert proof.matches(world.engine)
    assert proof.public_receipt()['runtime_endpoint']==ADDRESS
    assert proof.public_receipt()['data_origin']=='EXAMPLE'
    assert any('--address' in argv for argv in calls)
    assert any('unix://'+ADDRESS in argv for argv in calls)


@pytest.mark.parametrize('address',['tcp://example.invalid:1234','/tmp/foreign.sock',
    '/run/k3s/containerd/../other.sock','/run/containerd/containerd.sock',True,{},[]])
def test_other_endpoint_is_rejected_before_node_or_daemon_reads(monkeypatch,address):
    def forbidden(*args,**kwargs):raise AssertionError('No I/O for unsupported endpoint')
    monkeypatch.setattr(m,'kernel_identity',forbidden)
    with pytest.raises(ValueError,match='RUNTIME_ENDPOINT_UNSUPPORTED'):
        m.collect_local_node_image_proof({}, {},runner=forbidden,containerd_address=address)
