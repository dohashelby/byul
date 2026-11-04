"""서비스 상태와 선택 worker 조회의 경계. CPU 계약이며 GPU 실측이 아니다."""
import pytest

from guard.executors import lab_activation as activation


class StateLab:
    def __init__(self, census_result):
        self.target = {'pod': 'selected-second', 'pod_uid': 'selected-uid', 'pid': 37, 'start_ticks': 97}
        self.result = census_result
        self.missing = []; self.notes = []; self.census_calls = []; self.violations = []

    def miss(self, code): self.missing.append(code)
    def normal_request(self, label): self.notes.append({'step': label})
    def signal_worker(self, number):
        assert number == activation.POSIX_SIGKILL
        return self.target
    def wait(self, seconds): pass
    def requests(self, rows): return [{'outcome': 200}]
    def census(self, pod=None, pod_uid=None):
        self.census_calls.append((pod, pod_uid))
        if isinstance(self.result, Exception): raise self.result
        # A default first Pod could legitimately have no matching worker.
        return self.result if pod == self.target['pod'] else {'processes': []}
    def note(self, step, **facts): self.notes.append({'step': step, **facts})
    def violate(self, code): self.violations.append(code)


@pytest.mark.parametrize('error', [OSError('unavailable'), TimeoutError('deadline'), ValueError('replaced UID')])
def test_failed_or_replaced_census_remains_unavailable_instead_of_zero(error):
    lab = StateLab(error)
    activation._record_state(lab, {})
    assert lab.notes[-1]['processes'] is None
    assert 'STATE_FAILED_WORKER_CENSUS_UNAVAILABLE' in lab.missing
    assert lab.violations == []


def test_census_uses_the_signalled_pod_and_uid_instead_of_first_ready_pod():
    lab = StateLab({'processes': [{'pid': 38, 'start_ticks': 98}]})
    activation._record_state(lab, {})
    assert lab.census_calls == [('selected-second', 'selected-uid')]
    assert lab.notes[-1]['processes'] == 1
    assert lab.notes[-1]['target'] == lab.target
    assert lab.violations == []


def test_service_health_and_zero_selected_workers_are_not_a_bound_conflict():
    lab = StateLab({'processes': []})
    activation._record_state(lab, {})
    assert lab.notes[-1]['health'] == 200 and lab.notes[-1]['processes'] == 0
    assert lab.notes[-1]['health_worker_binding_verified'] is False
    assert 'STATE_SERVICE_HEALTH_WITHOUT_SELECTED_POD_ATTRIBUTION' in lab.missing
    assert 'STATE_HEALTH_LOG_WORKER_BINDING_UNOBSERVED' in lab.missing
    assert lab.violations == []
