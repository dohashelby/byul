"""B154 original bytes and real unchanged gate refusal on newer A common source."""
from pathlib import Path
import hashlib,socket,subprocess
import pytest
from tools import b_lif07_native_capture as native
from tools import verify_req10_consumption as gate
ROOT=Path(__file__).resolve().parents[2]
REVIEWED_B_BYTES={'update/docs/b-lif07/WORKER_READINESS.md': '71dfe9b6fe5e109fa547190e3cd60f92ce03c44cfe4986a01a55d0364bd619fe', 'update/docs/b-lif13/WEIGHTS_LIFETIME.md': '521b735829bd347a3fd2fd480b8997ddb147006596cb453afedf2d9930759e44', 'update/tests/linux_cpu/lif13_peer_probe.py': '789b08ff1acc549e478558fa8317eac074905e675d4cce87deede99f5d528e2d', 'update/tests/test_b_lif07_native_capture.py': '4bb9e469ca4c9f80bdb0c3b0a3b882bf79dba46be3258458c6665d953ab20ef7', 'update/tests/test_b_lif07_readiness.py': 'd484a8cfb4a2c10ba293ed435c364041f5834a87109a34758b6a8eff71cafaf8', 'update/tools/b_lif07_native_capture.py': '0e6cc9f1efb6776ccd6733b2938c61dba1132c5861e4b7c65a70b6bad001de82', 'update/tools/b_lif07_readiness.py': 'd23f736e62a3b7be7888202531f8d52f6a14fb971e2bbf5c75a70d7d2da79afa'}

def test_original_B154_seven_files_are_exact_confirmed_bytes():
    assert len(REVIEWED_B_BYTES)==7
    for name,digest in REVIEWED_B_BYTES.items():assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest
    assert native.GATE_SOURCE_COMMIT=='c39a36b7a6f306b910f0314b0deee10b55390dc4'
    assert native.REVIEWED_COMMON_COMMIT=='062af9d1e0bc76a9c6f180af40e2b9fe52ffeb89'

def test_new_A_full_common_pin_passes_while_original_B_native_pin_stays_closed():
    proof=gate.verify(ROOT)
    assert proof['files_verified']==385 and proof['paths_excluded']==[] and not proof['runtime_hash_approval']
    assert not proof['original_B_default_pin_changed']
    with pytest.raises(ValueError,match='^LIF07_NATIVE_APPROVED_SOURCE_GATE_UNAVAILABLE$'):native.verify_source()

def test_real_B_gate_rejects_bound_EXAMPLE_before_reader_or_remote_IO(tmp_path,monkeypatch):
    from tests.test_b_lif07_native_capture import make_inputs
    env,target,prepared,manifest=make_inputs(tmp_path)
    def forbidden(*args,**kwargs):raise AssertionError('unreviewed native capture reached reader or remote IO')
    monkeypatch.setattr(socket,'socket',forbidden);monkeypatch.setattr(subprocess,'Popen',forbidden)
    with pytest.raises(ValueError,match='^LIF07_NATIVE_APPROVED_SOURCE_GATE_UNAVAILABLE$'):
        native.collect_startup(env,target,prepared,manifest,_reader_factory=forbidden,_runner=forbidden)
