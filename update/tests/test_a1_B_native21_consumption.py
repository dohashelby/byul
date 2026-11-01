"""B160/PR171 history and B70431's explicitly reviewed native20 exact pair."""
from pathlib import Path
import ast,hashlib,socket,subprocess
import pytest
from tools import b_lif07_native_capture as native
from tools import verify_req10_consumption as gate
ROOT=Path(__file__).resolve().parents[2]
PREVIOUS_B154_BYTES={'update/docs/b-lif07/WORKER_READINESS.md': '71dfe9b6fe5e109fa547190e3cd60f92ce03c44cfe4986a01a55d0364bd619fe', 'update/docs/b-lif13/WEIGHTS_LIFETIME.md': '521b735829bd347a3fd2fd480b8997ddb147006596cb453afedf2d9930759e44', 'update/tests/linux_cpu/lif13_peer_probe.py': '789b08ff1acc549e478558fa8317eac074905e675d4cce87deede99f5d528e2d', 'update/tests/test_b_lif07_native_capture.py': '4bb9e469ca4c9f80bdb0c3b0a3b882bf79dba46be3258458c6665d953ab20ef7', 'update/tests/test_b_lif07_readiness.py': 'd484a8cfb4a2c10ba293ed435c364041f5834a87109a34758b6a8eff71cafaf8', 'update/tools/b_lif07_native_capture.py': '0e6cc9f1efb6776ccd6733b2938c61dba1132c5861e4b7c65a70b6bad001de82', 'update/tools/b_lif07_readiness.py': 'd23f736e62a3b7be7888202531f8d52f6a14fb971e2bbf5c75a70d7d2da79afa'}
REVIEWED_B_COMMIT='391506a71b8b444b89f9a852a426bc7c5fbad237'
REVIEWED_B_FIXTURE_COMMIT='140bcd7cb8d192ef07b6e1a24d6808085fbf977e'
REVIEWED_B_FIXTURE_BYTES={'update/tests/test_b_lif07_http_readiness.py': 'e89eed6b6eaf0f00201ca6efe872c33a892c29ab030cb3f1c0446924f2869076', 'update/tests/test_b_lif07_native_capture.py': '288e35bc9291319c598e7960d90bbbb4cb26b4a4e24d15d83673c89f90ae0415', 'update/tools/b_lif07_native_capture.py': '2bc76fb84bbd70aeffd6c03cb154520dbbee5fe0e0161c278c025f9efc02d646', 'update/tools/b_lif07_readiness.py': '7c3ef7f1b6055cac33b0f6f0216105d5cb102296d475a0024c74015c81c70346'}
REVIEWED_B_NATIVE20_COMMIT='70431e9e6fc2243cfe596aa0dbf543d5cb1b8121'
REVIEWED_B_NATIVE20_BYTES={
    'update/tools/b_lif07_native_capture.py':'92ec31fabbd459cb8716d80e0c1867b35ccb4cb8674730e4812403fba817983f',
    'update/tests/test_b_lif07_native_capture.py':'4592601d0f4d7dae53afbc0fce3846ae4780c13ee79ea853a5993fa9da613117',
    'update/tests/test_b_lif07_http_readiness.py':'a4bb91949ddeecaf52e1aa124a33b6bc62c901f5d18af60b568d797db1c84341',
    'update/tests/test_b_native20_common_consumption.py':'02de728aabc98aa44d27c491fd24ba0dcc2e6b7ec7a9876577987af61d4d00cc',
    'update/docs/b-lif07/PR171_SOURCE_CONSUMPTION.md':'52480575feae69b4a8acb74ee434c40962f11a10d2691ffd89bdcbab13d2d13c',
}
REVIEWED_B_NATIVE20_FIXTURE_COMMIT='3900e2ab832a9ec0a80391306033a00022d99d05'
REVIEWED_B_NATIVE20_FIXTURE_BYTES={
    'update/tests/test_b_lif07_native_capture.py':'48fc8801b3ffeaf4e215e8ba130e5b1b0623b45b9ff71a26c76c33c0ada6742a',
}
PREVIOUS_B154_REPAIRED_BYTES={'update/docs/b-lif07/WORKER_READINESS.md': '890309ab74ae1f26951dd2021edd44ff8fee9592cda89a58148785b7bbc71871', 'update/docs/b-lif13/WEIGHTS_LIFETIME.md': '074edb488ae421c5fb8f2b71b0efa80479c33f344c157b3b9f9ad1a9d61548f2', 'update/tests/test_b_lif07_native_capture.py': '4d5e54ff880ad8d1b8a76d0db97b28aeec8a1045e12ec9b64ccfb98cbd0d35e1', 'update/tests/test_b_lif07_readiness.py': '5f31b26ecf6629744b45cd64816d69984a12432448e498c96dd0074bbe5abb4c', 'update/tools/b_lif07_native_capture.py': 'af90b8015c9f99d27065304ad7f04bda46d8abccbbcfbbe8900c649d63055e3c', 'update/tools/b_lif07_readiness.py': '636057bf4a4a0aa34232ec6d29abf9152b6e72bd4647c149be92659dfba28f2e', 'update/tests/linux_cpu/lif13_peer_probe.py': '789b08ff1acc549e478558fa8317eac074905e675d4cce87deede99f5d528e2d'}
REVIEWED_B_BYTES={'update/docs/b-lif07/WORKER_READINESS.md': '5ede75e817cd57af5364be475d1d7eb0d6ff41423d37a2549019d405b5f2ab5f', 'update/docs/b-lif13/WEIGHTS_LIFETIME.md': '074edb488ae421c5fb8f2b71b0efa80479c33f344c157b3b9f9ad1a9d61548f2', 'update/tests/test_b_lif07_http_readiness.py': '8e8a9323132b32b7e5444cbc48954352dbab07809a5cf6bbfa04505c9226b1b0', 'update/tests/test_b_lif07_native_capture.py': '5376fc3eafbed4bf566ce48a1b1c4ca78936a74bb823971d5410c4c260f51585', 'update/tests/test_b_lif07_readiness.py': '5f31b26ecf6629744b45cd64816d69984a12432448e498c96dd0074bbe5abb4c', 'update/tools/b_lif07_native_capture.py': '3aa4ca2d21d9531859b6f19de5d5e7f206e3ec8d5f2050122275309e9417396d', 'update/tools/b_lif07_readiness.py': '172c726fa06a94a51963d3d20438178c96f02944ab42382d3de3e09f9e2e2dab', 'update/tests/linux_cpu/lif13_peer_probe.py': '789b08ff1acc549e478558fa8317eac074905e675d4cce87deede99f5d528e2d'}

def _current_native20_blob(name,original):
    if name not in REVIEWED_B_NATIVE20_FIXTURE_BYTES:
        return original
    successor=subprocess.check_output(['git','show',REVIEWED_B_NATIVE20_FIXTURE_COMMIT+':'+name],cwd=ROOT)
    assert hashlib.sha256(successor).hexdigest()==REVIEWED_B_NATIVE20_FIXTURE_BYTES[name]
    return successor


def test_original_B160_eight_files_are_exact_confirmed_bytes():
    assert len(REVIEWED_B_BYTES)==8
    for name,digest in REVIEWED_B_BYTES.items():
        # Keep B160's original eight approvals as historical bytes. The owner
        # later fixed these files; each exact successor is a separate review.
        original=subprocess.check_output(['git','show',REVIEWED_B_COMMIT+':'+name],cwd=ROOT)
        assert hashlib.sha256(original).hexdigest()==digest
        if name in REVIEWED_B_FIXTURE_BYTES:
            successor=subprocess.check_output(['git','show',REVIEWED_B_FIXTURE_COMMIT+':'+name],cwd=ROOT)
            assert hashlib.sha256(successor).hexdigest()==REVIEWED_B_FIXTURE_BYTES[name]
            if name in REVIEWED_B_NATIVE20_BYTES:
                current=subprocess.check_output(['git','show',REVIEWED_B_NATIVE20_COMMIT+':'+name],cwd=ROOT)
                assert hashlib.sha256(current).hexdigest()==REVIEWED_B_NATIVE20_BYTES[name]
                assert (ROOT/name).read_bytes()==_current_native20_blob(name,current)
            else:
                assert (ROOT/name).read_bytes()==successor
        else:
            assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest
    assert native.GATE_SOURCE_COMMIT=='c39a36b7a6f306b910f0314b0deee10b55390dc4'
    assert native.REVIEWED_COMMON_COMMIT=='062af9d1e0bc76a9c6f180af40e2b9fe52ffeb89'

def test_new_A_full_common_pin_is_the_exact_B_reviewed_pair():
    proof=native.verify_source()
    assert proof['files_verified']==394 and proof['paths_excluded']==[] and not proof['runtime_hash_approval']
    assert not proof['original_B_default_pin_changed']
    assert proof['reviewed_common_commit']==gate.REVIEWED_COMMON_COMMIT=='adcde856375a4de43d4a5d789c00de875e43e321'
    assert proof['verified_manifest_sha256']=='0ee4b9da536307f9f5d68b55f3b938c591260da3386dc401d6a01a51a9ebc3c2'


def test_B_native20_five_files_are_exact_owner_confirmed_bytes():
    assert len(REVIEWED_B_NATIVE20_BYTES)==5
    changed=subprocess.check_output(['git','diff-tree','--no-commit-id','--name-only','-r',REVIEWED_B_NATIVE20_COMMIT],cwd=ROOT).decode().splitlines()
    assert set(changed)==set(REVIEWED_B_NATIVE20_BYTES)
    for name,digest in REVIEWED_B_NATIVE20_BYTES.items():
        blob=subprocess.check_output(['git','show',REVIEWED_B_NATIVE20_COMMIT+':'+name],cwd=ROOT)
        assert hashlib.sha256(blob).hexdigest()==digest
        assert (ROOT/name).read_bytes()==_current_native20_blob(name,blob)


def test_B_owner_fixture_successor_preserves_product_sources_and_other_tests():
    changed=subprocess.check_output(['git','diff-tree','--no-commit-id','--name-only','-r',REVIEWED_B_NATIVE20_FIXTURE_COMMIT],cwd=ROOT).decode().splitlines()
    assert set(changed)==set(REVIEWED_B_NATIVE20_FIXTURE_BYTES)
    name,=REVIEWED_B_NATIVE20_FIXTURE_BYTES
    before=ast.parse(subprocess.check_output(['git','show',REVIEWED_B_NATIVE20_COMMIT+':'+name],cwd=ROOT))
    after=ast.parse(_current_native20_blob(name,b''))
    assert len(before.body)==len(after.body)
    differing=[getattr(left,'name',type(left).__name__) for left,right in zip(before.body,after.body)
               if ast.dump(left)!=ast.dump(right)]
    assert differing==['test_pr171_HTTP_known_common_scope_excludes_native_only_histories']
    assert native.HTTP_APPROVED_REVIEWED_COMMON_COMMITS==frozenset({
        'f277fecd6df1e59505c0ad731032593f6fb22885','df6255e96a877ebfbe185230ed87320aa84d2c30',
        'adcde856375a4de43d4a5d789c00de875e43e321'})
    assert len(native.APPROVED_GATE_PROFILES)==5


def test_public_PR171_392_snapshot_remains_historical_and_rejects_current394():
    old_gate=subprocess.check_output(['git','show','920be2dfc60084d1f7dead12609a6533edb9a220:update/tools/verify_req10_consumption.py'],cwd=ROOT)
    assert hashlib.sha256(old_gate).hexdigest()==native.PR171_GATE_SHA256=='00979cf6b5139e0337d1f7976acad2b816e1018c4422cf8a44a087b0c28fc233'
    names=subprocess.check_output(['git','ls-tree','-r','--name-only','-z',native.PR171_REVIEWED_COMMON_COMMIT,'--',*gate.PINNED_PATHS],cwd=ROOT).split(b'\0')
    assert len([n for n in names if n])==392
    from tools import b1_integration_cpu
    with pytest.raises(ValueError,match='^pinned source/index modified$'):
        b1_integration_cpu.verify_snapshot(ROOT,native.PR171_REVIEWED_COMMON_COMMIT,gate.PINNED_PATHS)

def test_real_B_gate_still_rejects_an_unapproved_pair_before_reader_or_remote_IO(tmp_path,monkeypatch):
    from tests.test_b_lif07_native_capture import make_inputs
    env,target,prepared,manifest=make_inputs(tmp_path)
    # Only the common is unregistered; the real gate bytes then form a mixed
    # pair. Its literal approval set remains intact and the gate is not replaced.
    monkeypatch.setattr(gate,'REVIEWED_COMMON_COMMIT','0'*40)
    assert gate.REVIEWED_COMMON_COMMIT not in native.APPROVED_REVIEWED_COMMON_COMMITS
    def forbidden(*args,**kwargs):raise AssertionError('unreviewed native capture reached reader or remote IO')
    monkeypatch.setattr(socket,'socket',forbidden);monkeypatch.setattr(subprocess,'Popen',forbidden)
    with pytest.raises(ValueError,match='^LIF07_NATIVE_APPROVED_SOURCE_GATE_UNAVAILABLE$'):
        native.collect_startup(env,target,prepared,manifest,_reader_factory=forbidden,_runner=forbidden)


def test_B_reviewed_exact_A152_A156_A158_PR171_native20_pairs_are_closed_and_literal():
    assert native.APPROVED_GATE_PROFILES==frozenset({
        ("5ac8204da0bc92858ab64591078d4ddb19aa2ed10a25f5437031611c1620282e","062af9d1e0bc76a9c6f180af40e2b9fe52ffeb89"),
        ("67b917029c6458fc16cc53be787134d652fdb096673721a486febe4d68a0c0b6","9d8987a5a306ffce28fcb735e012c400589303f2"),
        ("15701b1e83eab8665414456b3072bcad73ee2220ed02e9f63a06a3441b55d157","f277fecd6df1e59505c0ad731032593f6fb22885"),
        ("00979cf6b5139e0337d1f7976acad2b816e1018c4422cf8a44a087b0c28fc233","df6255e96a877ebfbe185230ed87320aa84d2c30"),
        ("61c29e98ece3c27c3fb978c180b86c6ef1a2ba6bf60edcf9263a2fc3943acab7","adcde856375a4de43d4a5d789c00de875e43e321")})
    assert gate.REVIEWED_COMMON_COMMIT=="adcde856375a4de43d4a5d789c00de875e43e321"
    assert native.NATIVE20_GATE_SOURCE_COMMIT=='d8904d0ee9d666d9d0070dbf2003b15f4e0b14bd'
    assert gate.REVIEWED_COMMON_COMMIT in native.APPROVED_REVIEWED_COMMON_COMMITS
    assert PREVIOUS_B154_BYTES["update/tests/linux_cpu/lif13_peer_probe.py"]==REVIEWED_B_BYTES["update/tests/linux_cpu/lif13_peer_probe.py"]
