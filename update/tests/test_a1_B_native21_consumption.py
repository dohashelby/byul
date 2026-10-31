"""B160 fixed original bytes and explicitly reviewed A152/A156/A158 exact pairs."""
from pathlib import Path
import hashlib,socket,subprocess
import pytest
from tools import b_lif07_native_capture as native
from tools import verify_req10_consumption as gate
ROOT=Path(__file__).resolve().parents[2]
PREVIOUS_B154_BYTES={'update/docs/b-lif07/WORKER_READINESS.md': '71dfe9b6fe5e109fa547190e3cd60f92ce03c44cfe4986a01a55d0364bd619fe', 'update/docs/b-lif13/WEIGHTS_LIFETIME.md': '521b735829bd347a3fd2fd480b8997ddb147006596cb453afedf2d9930759e44', 'update/tests/linux_cpu/lif13_peer_probe.py': '789b08ff1acc549e478558fa8317eac074905e675d4cce87deede99f5d528e2d', 'update/tests/test_b_lif07_native_capture.py': '4bb9e469ca4c9f80bdb0c3b0a3b882bf79dba46be3258458c6665d953ab20ef7', 'update/tests/test_b_lif07_readiness.py': 'd484a8cfb4a2c10ba293ed435c364041f5834a87109a34758b6a8eff71cafaf8', 'update/tools/b_lif07_native_capture.py': '0e6cc9f1efb6776ccd6733b2938c61dba1132c5861e4b7c65a70b6bad001de82', 'update/tools/b_lif07_readiness.py': 'd23f736e62a3b7be7888202531f8d52f6a14fb971e2bbf5c75a70d7d2da79afa'}
REVIEWED_B_COMMIT='391506a71b8b444b89f9a852a426bc7c5fbad237'
PREVIOUS_B154_REPAIRED_BYTES={'update/docs/b-lif07/WORKER_READINESS.md': '890309ab74ae1f26951dd2021edd44ff8fee9592cda89a58148785b7bbc71871', 'update/docs/b-lif13/WEIGHTS_LIFETIME.md': '074edb488ae421c5fb8f2b71b0efa80479c33f344c157b3b9f9ad1a9d61548f2', 'update/tests/test_b_lif07_native_capture.py': '4d5e54ff880ad8d1b8a76d0db97b28aeec8a1045e12ec9b64ccfb98cbd0d35e1', 'update/tests/test_b_lif07_readiness.py': '5f31b26ecf6629744b45cd64816d69984a12432448e498c96dd0074bbe5abb4c', 'update/tools/b_lif07_native_capture.py': 'af90b8015c9f99d27065304ad7f04bda46d8abccbbcfbbe8900c649d63055e3c', 'update/tools/b_lif07_readiness.py': '636057bf4a4a0aa34232ec6d29abf9152b6e72bd4647c149be92659dfba28f2e', 'update/tests/linux_cpu/lif13_peer_probe.py': '789b08ff1acc549e478558fa8317eac074905e675d4cce87deede99f5d528e2d'}
REVIEWED_B_BYTES={'update/docs/b-lif07/WORKER_READINESS.md': '5ede75e817cd57af5364be475d1d7eb0d6ff41423d37a2549019d405b5f2ab5f', 'update/docs/b-lif13/WEIGHTS_LIFETIME.md': '074edb488ae421c5fb8f2b71b0efa80479c33f344c157b3b9f9ad1a9d61548f2', 'update/tests/test_b_lif07_http_readiness.py': '8e8a9323132b32b7e5444cbc48954352dbab07809a5cf6bbfa04505c9226b1b0', 'update/tests/test_b_lif07_native_capture.py': '5376fc3eafbed4bf566ce48a1b1c4ca78936a74bb823971d5410c4c260f51585', 'update/tests/test_b_lif07_readiness.py': '5f31b26ecf6629744b45cd64816d69984a12432448e498c96dd0074bbe5abb4c', 'update/tools/b_lif07_native_capture.py': '3aa4ca2d21d9531859b6f19de5d5e7f206e3ec8d5f2050122275309e9417396d', 'update/tools/b_lif07_readiness.py': '172c726fa06a94a51963d3d20438178c96f02944ab42382d3de3e09f9e2e2dab', 'update/tests/linux_cpu/lif13_peer_probe.py': '789b08ff1acc549e478558fa8317eac074905e675d4cce87deede99f5d528e2d'}

def test_original_B160_eight_files_are_exact_confirmed_bytes():
    assert len(REVIEWED_B_BYTES)==8
    for name,digest in REVIEWED_B_BYTES.items():assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest
    assert native.GATE_SOURCE_COMMIT=='c39a36b7a6f306b910f0314b0deee10b55390dc4'
    assert native.REVIEWED_COMMON_COMMIT=='062af9d1e0bc76a9c6f180af40e2b9fe52ffeb89'

def test_new_A_full_common_pin_passes_while_unreviewed_B160_pair_is_closed():
    proof=gate.verify(ROOT)
    assert proof['files_verified']==389 and proof['paths_excluded']==[] and not proof['runtime_hash_approval']
    assert not proof['original_B_default_pin_changed']
    assert gate.REVIEWED_COMMON_COMMIT not in native.APPROVED_REVIEWED_COMMON_COMMITS
    with pytest.raises(ValueError,match='^LIF07_NATIVE_APPROVED_SOURCE_GATE_UNAVAILABLE$'):native.verify_source()

def test_real_B_gate_still_rejects_an_unapproved_pair_before_reader_or_remote_IO(tmp_path,monkeypatch):
    from tests.test_b_lif07_native_capture import make_inputs
    env,target,prepared,manifest=make_inputs(tmp_path)
    # The new A common pair has not been reviewed by B. Its literal approval
    # set remains intact; no source gate replacement is used here.
    assert gate.REVIEWED_COMMON_COMMIT not in native.APPROVED_REVIEWED_COMMON_COMMITS
    def forbidden(*args,**kwargs):raise AssertionError('unreviewed native capture reached reader or remote IO')
    monkeypatch.setattr(socket,'socket',forbidden);monkeypatch.setattr(subprocess,'Popen',forbidden)
    with pytest.raises(ValueError,match='^LIF07_NATIVE_APPROVED_SOURCE_GATE_UNAVAILABLE$'):
        native.collect_startup(env,target,prepared,manifest,_reader_factory=forbidden,_runner=forbidden)


def test_B_reviewed_exact_A152_A156_A158_pairs_are_closed_and_literal():
    assert native.APPROVED_GATE_PROFILES==frozenset({
        ("5ac8204da0bc92858ab64591078d4ddb19aa2ed10a25f5437031611c1620282e","062af9d1e0bc76a9c6f180af40e2b9fe52ffeb89"),
        ("67b917029c6458fc16cc53be787134d652fdb096673721a486febe4d68a0c0b6","9d8987a5a306ffce28fcb735e012c400589303f2"),
        ("15701b1e83eab8665414456b3072bcad73ee2220ed02e9f63a06a3441b55d157","f277fecd6df1e59505c0ad731032593f6fb22885")})
    assert gate.REVIEWED_COMMON_COMMIT=="d2ef4b9cf6010fc940b113f764170c355b1c7f15"
    assert gate.REVIEWED_COMMON_COMMIT not in native.APPROVED_REVIEWED_COMMON_COMMITS
    assert PREVIOUS_B154_BYTES["update/tests/linux_cpu/lif13_peer_probe.py"]==REVIEWED_B_BYTES["update/tests/linux_cpu/lif13_peer_probe.py"]
