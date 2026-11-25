"""One normal completion, bound engine logs, and no retained source values."""
from datetime import timedelta
import json

import pytest

from guard.adapters.vllm import VllmAdapter
from guard.core.judge import judge_evidence
from guard.core.run import _normalize
from guard.executors import kubernetes_log_analysis
from guard.executors.kubernetes_log_analysis import KubernetesLogAnalysisExecutor, LIMIT_BYTES
from guard.models import Evidence, ExecStatus, canonical_json, load_pack, sha256_hex, utcnow
from tests.builders import T0, dummy_ref, policy
from tests.test_kubernetes_process_consumers import ProcessWorld
from tests.test_model_preparation_integration import full_inputs
from tools import mvp_live_readiness


VALUES = {'required_sources': ['engine', 'gateway'], 'required_branches': ['normal', 'error', 'exception_dump'],
          'allowed_log_fields': ['request_id', 'event'], 'forbidden_log_fields': ['prompt'],
          'forbidden_marker_contexts': ['caller_input', 'error_input']}
PRIVATE = 'EXAMPLE-PRIVATE-LOG-BODY-CANNOT-BE-PERSISTED'
STDERR = 'EXAMPLE-PRIVATE-STDERR-CANNOT-BE-PERSISTED'
REQUEST_ID = 'cmpl-EXAMPLE-NORMAL-CORRELATION'
WINDOW_STAMPS = {
    'at-window-end': '2026-10-19T00:00:01.000000000Z',
    'same-end-with-offset': '2026-10-19T01:00:01.000000000+01:00',
    'one-nanosecond-after-window': '2026-10-19T00:00:01.000000001Z',
    'unknown-window-offset': '2026-10-19T00:00:01.000000000-00:00',
}


class LogWorld(ProcessWorld):
    def __init__(self, target, mode):
        super().__init__(target)
        self.log_mode = mode
        self.log_reads = 0

    def runner(self, argv, **kwargs):
        if 'logs' in argv:
            self.log_reads += 1
            assert '--timestamps' in argv and f'--limit-bytes={LIMIT_BYTES}' in argv
            assert self.target['kubernetes']['engine']['pod'] in argv
            assert '--container=' + self.target['kubernetes']['engine']['container'] in argv
            event = {'request_id': REQUEST_ID if self.log_mode != 'unlinked' else 'cmpl-unrelated-event', 'event': PRIVATE}
            if self.log_mode != 'clean':
                event.update(prompt=PRIVATE, **{PRIVATE: PRIVATE})
            stamp = WINDOW_STAMPS.get(self.log_mode) or utcnow().isoformat()
            text = f'{stamp} {json.dumps(event)}\n' if self.log_mode != 'plain' else f'{stamp} {PRIVATE}\n'
            raw = text.encode()
            if self.log_mode == 'invalid-utf8':
                raw = raw.replace(PRIVATE.encode(), b'\xff', 1)
            elif self.log_mode == 'unterminated':
                raw = raw.removesuffix(b'\n')
            elif self.log_mode == 'split-timestamp':
                raw = raw.replace(b' {', b'\r{', 1)
            elif self.log_mode == 'mixed-invalid':
                raw = raw.replace(PRIVATE.encode(), b'\xff', 1) + raw
            if self.log_mode == 'replaced':
                self.replaced = True
            pipes = {'schema': 'guard-pipe-capture/1', 'complete': True, 'bounded_at_source': True,
                     'timed_out': False, 'truncated': False, 'pipes_closed': True, 'read_errors': []}
            returncode = {'nonzero': 1, 'unreaped': -1}.get(self.log_mode, 0)
            if self.log_mode == 'unreaped':
                pipes.update(complete=False, timed_out=True, pipes_closed=False, read_errors=['child_reap'])
            return {'returncode': returncode, 'stdout_bytes': raw, 'stderr_bytes': STDERR.encode(),
                    'capture': {**pipes, 'streams': {'stderr': {'retained_sha256': sha256_hex(STDERR.encode())}}}}
        result = super().runner(argv, **kwargs)
        if 'exec' in argv and 'input_bytes' in kwargs:
            if self.log_mode == 'normal-unavailable':
                return {**result, 'returncode': 1, 'stderr_bytes': STDERR.encode()}
            row, = json.loads(result['stdout_bytes'])
            body = json.loads(row['body'])
            body['id'] = REQUEST_ID
            text = json.dumps(body)
            row.update(body=text, body_bytes=len(text.encode()), body_sha256=sha256_hex(text.encode()))
            result['stdout_bytes'] = canonical_json([row])
        return result


@pytest.mark.parametrize('mode,security', [('clean', 'UNKNOWN'), ('forbidden', 'FAIL'), ('unlinked', 'UNKNOWN'),
                                            ('plain', 'UNKNOWN'), ('replaced', 'UNKNOWN'), ('nonzero', 'UNKNOWN'),
                                            ('unreaped', 'UNKNOWN'), ('normal-unavailable', 'UNKNOWN'),
                                            ('invalid-utf8', 'UNKNOWN'), ('unterminated', 'UNKNOWN'),
                                            ('split-timestamp', 'UNKNOWN'), ('mixed-invalid', 'FAIL'),
                                            ('at-window-end', 'FAIL'), ('same-end-with-offset', 'FAIL'),
                                            ('one-nanosecond-after-window', 'UNKNOWN'),
                                            ('unknown-window-offset', 'UNKNOWN')])
def test_owned_log_projection_keeps_only_correlated_field_predicates(tmp_path, mode, security, monkeypatch):
    if mode in WINDOW_STAMPS:
        bounds = iter((T0, T0 + timedelta(seconds=1)))
        monkeypatch.setattr(kubernetes_log_analysis, 'utcnow', lambda: next(bounds))
    env, target = full_inputs()
    world = LogWorld(target, mode)
    path = tmp_path / 'target.json'
    path.write_bytes(canonical_json(target))
    pack = load_pack(mvp_live_readiness.ROOT / 'packs/PACK-BASELINE')
    check = pack.checks['OBS-03']
    draft = KubernetesLogAnalysisExecutor({'target': str(path)}, runner=world.runner).run(
        check, env, VALUES, VllmAdapter(), tmp_path / 'work')
    assert len(world.packets) == world.log_reads == 1
    request, = world.packets[0]
    assert request['headers'] == {} and request['body']['prompt'] == 'ping'
    assert all(not challenge.attempted for challenge in draft.challenges)
    assert draft.data_origin.value == 'EXAMPLE'
    log_unavailable = mode in {'replaced', 'nonzero', 'unreaped'}
    assert draft.status == (ExecStatus.ABORTED if log_unavailable else ExecStatus.COMPLETED)
    assert draft.abort_reason == ('OWNED_ENGINE_LOG_CAPTURE_INCOMPLETE' if log_unavailable else None)
    assert draft.cleanup_errors == []
    assert draft.observations['log_coverage_complete'].value is False
    assert draft.observations['log_fields_seen'].collected is (mode in {
        'clean', 'forbidden', 'mixed-invalid', 'at-window-end', 'same-end-with-offset',
    })
    assert draft.observations['log_marker_contexts_seen'].collected is False
    evd = Evidence(id='EVD-OBS03-EXAMPLE', data_origin=draft.data_origin, check_id=check.id, side='single',
                   environment=env.ref(), plan=dummy_ref('PLAN'), execution={'status': draft.status},
                   **_normalize(check, draft)).seal()
    assert 'collection_status' not in evd.observations
    result = judge_evidence(check, evd, policy(pack.ref(), values={'OBS-03': VALUES}), T0)
    assert result.verdicts.security == security
    artifact, = draft.artifacts
    persisted = artifact.path.read_bytes()
    private_fields = (PRIVATE, STDERR, REQUEST_ID, sha256_hex(STDERR.encode()))
    assert all(value.encode() not in persisted + canonical_json(evd.to_dict()) for value in private_fields)
    summary = json.loads(persisted.splitlines()[-1])
    status = summary['collection_status']
    generic = {'stage': 'binding_or_capture_unavailable'}
    assert set(status) == {'normal_control', 'engine_stdout'}
    if mode == 'normal-unavailable':
        assert status['normal_control'] == generic
        assert not draft.normal_control.succeeded
    else:
        assert status['normal_control'] == {'stage': 'response_id_observed', 'binding_verified_before_after': True,
                                            'capture_complete': True, 'http_status': 200}
    unreaped = mode == 'unreaped'
    if mode == 'replaced':
        assert status['engine_stdout'] == generic
    else:
        assert status['engine_stdout'] == {
            'stage': 'runner_returned_nonzero' if log_unavailable else 'bounded_capture_complete',
            'returncode': {'nonzero': 1, 'unreaped': -1}.get(mode, 0),
            'bounded_at_source': True, 'complete': not unreaped, 'timed_out': unreaped,
            'truncated': False, 'pipes_closed': not unreaped, 'child_reap_unverified': unreaped,
        }
    assert summary['source']['available'] == (not log_unavailable)
    if mode in {'one-nanosecond-after-window', 'unknown-window-offset'}:
        assert 'engine: exact trial event lies outside or has an invalid capture timestamp' in summary['gaps']
    if mode in {'invalid-utf8', 'unterminated', 'split-timestamp', 'mixed-invalid'}:
        assert 'ENGINE_LOG_FORMAT_PARTIAL' in summary['gaps']
    if log_unavailable:
        assert summary['source'] == {'available': False, 'byte_count': None, 'sha256': None, 'payload_retained': False}
