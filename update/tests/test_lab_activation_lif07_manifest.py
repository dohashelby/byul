"""LIF07 controlled manifest transition retains incomplete readiness attribution."""
import hashlib

import pytest

from guard.executors.lab_activation import Lab, _startup_gate


class Startup:
    cfg = {'model': 'before', 'deadline_ms': 1000,
        'update_manifest': '/unused/next.json', 'update_manifest_sha256': 'a' * 64}

    def __init__(self):
        self.t = 0.0
        self.applied = []
        self.missing = []
        self.steps = []

    def clock(self):
        self.t += 0.01
        return self.t

    def wait(self, duration):
        self.t += duration

    def _pods(self):
        uid = 'new' if self.applied else 'old'
        return [{'metadata': {'uid': uid}, 'status': {'conditions': [
            {'type': 'Ready', 'status': 'True'}]}}]

    def apply(self, name):
        self.applied.append(name)

    def rollout(self):
        pytest.fail('pinned update manifest must be applied')

    def normal_request(self, _label, *, model):
        return {'outcome': 200, 'complete': True, 'model': model}

    def chat(self, **_kwargs):
        return {}

    def requests(self, _items):
        return [{'outcome': 200, 'model': 'before'}]

    def miss(self, code):
        self.missing.append(code)

    def note(self, label, **facts):
        self.steps.append((label, facts))


def test_lif07_uses_explicit_pinned_manifest_but_keeps_native_attribution_gap():
    lab = Startup()
    _startup_gate(lab, {})
    assert lab.applied == ['update_manifest']
    assert 'STARTUP_NATIVE_RANK_MODEL_BACKEND_UNOBSERVED' in lab.missing
    assert 'STARTUP_RESPONSE_POD_UNATTRIBUTED' in lab.missing
    assert 'STARTUP_RANK_DELAY_CHALLENGE_UNIMPLEMENTED' in lab.missing
    assert 'STARTUP_THREE_CASE_CHALLENGES_UNIMPLEMENTED' in lab.missing
    assert 'STARTUP_TRANSITION_NATIVE_SESSION_UNBOUND' in lab.missing
    assert lab.steps[-1][1]['new_generation_ready'] is True
    assert lab.steps[-1][1]['manifest_apply_attempted'] is True
    assert lab.steps[-1][1]['new_pod_uids_seen'] == ['new']


def test_lif07_changed_update_manifest_is_rejected_before_kubectl(tmp_path):
    manifest = tmp_path / 'update.json'
    manifest.write_text('{"generation":"approved"}')
    expected = hashlib.sha256(manifest.read_bytes()).hexdigest()
    manifest.write_text('{"generation":"changed"}')
    cfg = {'namespace': 'lab', 'selector': 'app=vllm', 'container': 'engine',
        'probe_pod': 'probe', 'probe_container': 'probe', 'service_url': 'http://vllm.lab.svc',
        'model': 'before', 'deployment': 'vllm', 'process_pattern': 'VLLM',
        'deadline_ms': 1000, 'update_manifest': str(manifest),
        'update_manifest_sha256': expected}
    lab = Lab(cfg, runner=lambda *_args, **_kwargs: pytest.fail('kubectl apply attempted'))
    with pytest.raises(ValueError, match='LAB_ACTIVATION_UPDATE_MANIFEST_CHANGED'):
        lab.apply('update_manifest')
    assert lab.mutated is False


def test_lif07_noop_manifest_and_old_pod_200_do_not_count_as_new_generation():
    class Noop(Startup):
        def _pods(self):
            return [{'metadata': {'uid': 'old'}, 'status': {'conditions': [
                {'type': 'Ready', 'status': 'True'}]}}]

    lab = Noop()
    _startup_gate(lab, {})
    assert lab.applied == ['update_manifest']
    assert 'STARTUP_TRANSITION_GENERATION_UNOBSERVED' in lab.missing
    assert 'STARTUP_NOT_READY_WITHIN_DEADLINE' in lab.missing
    assert lab.steps[-1][1]['new_pod_uids_seen'] == []
    assert lab.steps[-1][1]['new_generation_ready'] is False


def test_lif07_scope_missing_does_not_erase_prior_independent_violation():
    lab = Startup()
    lab.violations = [{'code': 'CONFIRMED_INDEPENDENT_FAIL'}]
    _startup_gate(lab, {})
    assert lab.violations == [{'code': 'CONFIRMED_INDEPENDENT_FAIL'}]
    assert 'STARTUP_RANK_DELAY_CHALLENGE_UNIMPLEMENTED' in lab.missing
