"""Exact reviewed source profiles; current source gate is exercised without target IO."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import pytest
from guard.runtime import reviewed_http_contract as contract
from tools import b_lif07_native_capture as helper
from tools import b_lif07_readiness as tool
from tests.test_b_lif07_http_readiness import configure, collect
from tests.test_b_lif07_readiness import case


def test_current_raw_and_offline_wrapper_literals_match_without_manifest_approval():
    from guard.runtime.kubernetes_http import KubernetesHTTP, ENGINE_CALLBACK_PROFILE, BINDING_PROBE_SHA256
    from tests.test_kubernetes_request_binding import env, target
    repo = Path(__file__).resolve().parents[2]
    for path, literal in contract._CURRENT_RAW.items():
        assert sha256((repo / path).read_bytes()).hexdigest() == literal
    assert BINDING_PROBE_SHA256 == contract._CURRENT_RAW['update/src/guard/executors/stream_probe.py']
    e = env()
    transport = KubernetesHTTP(target(e), e, duration=1, profile=ENGINE_CALLBACK_PROFILE,
        runner=lambda *a, **k: (_ for _ in ()).throw(AssertionError('offline constructor must not call target')))
    assert transport.source_sha256 == contract._CURRENT_WRAPPER


def test_current_full_gate_and_offline_wrapper_match_literal():
    proof = helper.verify_source()
    sources = contract.expected_http_sources(proof)
    repo = Path(__file__).resolve().parents[2]
    probe = (repo / 'update/src/guard/executors/stream_probe.py').read_text(encoding='utf-8')
    source = ('import math,os,sys,threading\nlimit=float(sys.argv[1])\n'
        'if not math.isfinite(limit) or not 0 < limit <= 120: sys.exit(124)\n'
        'watchdog=threading.Timer(limit,lambda:os._exit(124))\nwatchdog.daemon=True\nwatchdog.start()\n'
        + 'exec(compile(' + repr(probe) + ",'guard-stream-probe','exec'),{'__name__':'__main__'})\n")
    assert sources['probe'] == sha256(source.encode()).hexdigest() == contract._CURRENT_WRAPPER
    assert sources['probe'] != sha256(probe.encode()).hexdigest()
    assert helper.HTTP_EXPECTED_SOURCES == contract._LEGACY
    assert proof['paths_excluded'] == [] and not proof['runtime_hash_approval']


@pytest.mark.parametrize('common', sorted(contract._LEGACY_COMMON))
def test_historical_profiles_keep_original_literals(common):
    result = contract.expected_http_sources({'reviewed_common_commit': common})
    assert result == helper.HTTP_EXPECTED_SOURCES
    result['probe'] = 'changed'
    assert contract._LEGACY == helper.HTTP_EXPECTED_SOURCES


@pytest.mark.parametrize('path', sorted(contract._CURRENT_RAW))
def test_unregistered_source_combination_closes_before_collector_or_target_IO(case, monkeypatch, path):
    world, created, window = configure(case, monkeypatch)
    # Real current proof, then an unregistered literal combination in the resolver.
    from tools import verify_req10_consumption as gate
    proof = gate.verify(Path(__file__).resolve().parents[2])
    monkeypatch.setattr(helper, 'verify_source', lambda: proof)
    changed = deepcopy(contract._CURRENT_RAW); changed[path] = '0' * 64
    monkeypatch.setattr(contract, '_CURRENT_RAW', changed)
    observation = collect(case, window)
    assert observation['gaps'] == ['HTTP_SOURCE_UNAVAILABLE']
    assert observation['snapshot'] is None and not observation['sample_attempted']
    assert created == [] and world.calls == [] and world.execs == []


@pytest.mark.parametrize('proof', [None, {}, {'reviewed_common_commit': []}, {'reviewed_common_commit': '0'*64}])
def test_unknown_or_malformed_proof_rejected(proof):
    with pytest.raises(ValueError, match='HTTP_REVIEWED_CONTRACT_UNAVAILABLE'):
        contract.expected_http_sources(proof)
