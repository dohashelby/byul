"""기존 IPC 정상 대조 응답 대응과 독립 위반 보존 회귀."""
import json

import pytest

from guard.models import ExecStatus, Verdict
from tests.test_unix_ipc import World, run


class ResponseWorld(World):
    def __init__(self, mode, connected=False):
        super().__init__(connected=connected)
        self.mode = mode

    def probe(self, net, requests, timeout):
        rows = super().probe(net, requests, timeout)
        if not any(r['method'] == 'POST' for r in requests):
            return rows
        if self.mode == 'legacy':
            for row in rows: row.pop('id')
        elif self.mode == 'wrong-id': rows[0]['id'] = 'EXAMPLE-unrelated-response'
        elif self.mode == 'null-id': rows[0]['id'] = None
        elif self.mode == 'bool-id': rows[0]['id'] = True
        elif self.mode == 'extra': rows += [dict(rows[0])]
        elif self.mode == 'missing': rows = []
        elif self.mode == 'non-row': rows = ['EXAMPLE_PRIVATE_UNTRUSTED_RESPONSE']
        elif self.mode == 'tuple': rows = tuple(rows)
        elif self.mode == 'dictionary': rows = {'EXAMPLE_PRIVATE_UNTRUSTED_RESPONSE': rows[0]}
        return rows


@pytest.mark.parametrize('mode', ['valid', 'legacy'])
@pytest.mark.parametrize('connected', [False, True])
def test_supported_response_contract_keeps_normal_control_and_prior_verdict(tmp_path, monkeypatch, mode, connected):
    _, _, _, evd, result, _ = run(tmp_path, monkeypatch, ResponseWorld(mode, connected))
    assert evd.execution.status == ExecStatus.COMPLETED
    assert evd.normal_control.succeeded is True
    assert evd.observations['functional_probe_results'].value == {'POST /v1/completions':'pass'}
    assert result.verdicts.security == (Verdict.FAIL if connected else Verdict.UNKNOWN)
    assert result.verdicts.functional == result.verdicts.performance == Verdict.UNKNOWN


@pytest.mark.parametrize('mode', ['wrong-id', 'null-id', 'bool-id', 'extra', 'missing',
                                  'non-row', 'tuple', 'dictionary'])
@pytest.mark.parametrize('connected', [False, True])
def test_uncorrelated_response_is_unknown_control_and_cannot_erase_ipc_violation(tmp_path, monkeypatch, mode, connected):
    store, _, _, evd, result, _ = run(tmp_path, monkeypatch, ResponseWorld(mode, connected))
    assert evd.execution.status == ExecStatus.COMPLETED and evd.data_origin.value == 'EXAMPLE'
    assert evd.normal_control.attempted and evd.normal_control.succeeded is False
    assert evd.observations['functional_probe_results'].value == {'POST /v1/completions':'no_response'}
    assert bool(evd.observations['unix_ipc_violations'].value) is connected
    assert result.verdicts.security == (Verdict.FAIL if connected else Verdict.UNKNOWN)
    assert result.verdicts.functional == result.verdicts.performance == Verdict.UNKNOWN
    assert evd.observations['ipc_scope_complete'].value is False
    summary = (store.root/evd.artifacts[0].path).read_text(encoding='utf-8')
    assert 'IPC_NORMAL_CONTROL_RESPONSE_UNCORRELATED' in summary
    assert 'PRIVATE_' not in summary and 'EXAMPLE-unrelated-response' not in summary
    assert 'PRIVATE_' not in json.dumps(evd.to_dict())
