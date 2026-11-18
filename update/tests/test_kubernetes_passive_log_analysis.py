"""One normal completion, bound engine logs, and no retained source values."""
import json

import pytest

from guard.adapters.vllm import VllmAdapter
from guard.core.judge import judge_evidence
from guard.executors.kubernetes_log_analysis import KubernetesLogAnalysisExecutor, LIMIT_BYTES
from guard.models import ExecStatus, canonical_json, load_pack, sha256_hex, utcnow
from tests.builders import T0, evidence, policy
from tests.test_kubernetes_process_consumers import ProcessWorld
from tests.test_model_preparation_integration import full_inputs
from tools import mvp_live_readiness


VALUES = {'required_sources': ['engine', 'gateway'], 'required_branches': ['normal', 'error', 'exception_dump'],
          'allowed_log_fields': ['request_id', 'event'], 'forbidden_log_fields': ['prompt'],
          'forbidden_marker_contexts': ['caller_input', 'error_input']}
PRIVATE = 'EXAMPLE-PRIVATE-LOG-BODY-CANNOT-BE-PERSISTED'
REQUEST_ID = 'cmpl-EXAMPLE-NORMAL-CORRELATION'


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
            stamp = utcnow().isoformat()
            text = f'{stamp} {json.dumps(event)}\n' if self.log_mode != 'plain' else f'{stamp} {PRIVATE}\n'
            if self.log_mode == 'replaced':
                self.replaced = True
            return {'returncode': 0, 'stdout_bytes': text.encode(), 'stderr_bytes': PRIVATE.encode(),
                    'capture': {'complete': True, 'bounded_at_source': True, 'timed_out': False, 'truncated': False}}
        result = super().runner(argv, **kwargs)
        if 'exec' in argv and 'input_bytes' in kwargs:
            row, = json.loads(result['stdout_bytes'])
            body = json.loads(row['body'])
            body['id'] = REQUEST_ID
            text = json.dumps(body)
            row.update(body=text, body_bytes=len(text.encode()), body_sha256=sha256_hex(text.encode()))
            result['stdout_bytes'] = canonical_json([row])
        return result


@pytest.mark.parametrize('mode,security', [('clean', 'UNKNOWN'), ('forbidden', 'FAIL'), ('unlinked', 'UNKNOWN'),
                                            ('plain', 'UNKNOWN'), ('replaced', 'UNKNOWN')])
def test_owned_log_projection_keeps_only_correlated_field_predicates(tmp_path, mode, security):
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
    assert draft.status == (ExecStatus.ABORTED if mode == 'replaced' else ExecStatus.COMPLETED)
    assert draft.observations['log_coverage_complete'].value is False
    assert draft.observations['log_fields_seen'].collected is (mode in {'clean', 'forbidden'})
    assert draft.observations['log_marker_contexts_seen'].collected is False
    observed = {key: entry.model_dump() for key, entry in draft.observations.items()}
    result = judge_evidence(check, evidence(status=draft.status, check_id='OBS-03', observations=observed,
                                          normal=draft.normal_control.model_dump()),
                            policy(pack.ref(), values={'OBS-03': VALUES}), T0)
    assert result.verdicts.security == security
    persisted = b''.join(artifact.path.read_bytes() for artifact in draft.artifacts)
    assert PRIVATE.encode() not in persisted and REQUEST_ID.encode() not in persisted
