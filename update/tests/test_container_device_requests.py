"""EXAMPLE regression for GPU DeviceRequests omitted on the owned L4 host."""
from copy import deepcopy

import pytest

from guard.models import Verdict
from tests.test_container_config import FakeDocker, _run, _summary


@pytest.mark.parametrize('profile,expected', [
    ('hardened', Verdict.UNKNOWN), ('overprivileged', Verdict.FAIL),
])
def test_gpu_device_declarations_never_prove_effective_worker_access(tmp_path, profile, expected):
    store, evd, result = _run(tmp_path, FakeDocker(profile), gpu_count=1)
    assert result.verdicts.security == expected
    assert evd.observations['devices'].collected is False
    assert evd.observations['devices'].value is None
    summary = _summary(store, evd)
    assert summary['devices'] == []
    assert summary['gpu_effective_device_access_unverified'] is True
    assert 'GPU_EFFECTIVE_DEVICE_ACCESS_UNVERIFIED' in summary['collection_errors']


@pytest.mark.parametrize('requests,known_device,profile,expected', [
    (None, False, 'hardened', Verdict.PASS),
    ([], False, 'hardened', Verdict.PASS),
    ([{'Driver': '', 'Count': -1, 'DeviceIDs': None, 'Capabilities': [['gpu']], 'Options': {}}],
     False, 'hardened', Verdict.UNKNOWN),
    ([{'Driver': 'nvidia', 'Count': 1, 'Capabilities': [['gpu']]}], True, 'hardened', Verdict.FAIL),
    ({'malformed': 'EXAMPLE'}, False, 'hardened', Verdict.UNKNOWN),
    ([{'Driver': 'nvidia', 'Count': 1, 'Capabilities': [['gpu']]}], False, 'overprivileged', Verdict.FAIL),
])
def test_unresolved_device_requests_cannot_prove_empty_devices_or_erase_known_fail(
        tmp_path, requests, known_device, profile, expected):
    class RequestedDocker(FakeDocker):
        def inspect_json(self, container, *, timeout):
            row = super().inspect_json(container, timeout=timeout)
            row['HostConfig']['DeviceRequests'] = deepcopy(requests)
            if known_device:
                row['HostConfig']['Devices'] = [{'PathOnHost': '/dev/EXAMPLE-unapproved',
                    'PathInContainer': '/dev/EXAMPLE-unapproved', 'CgroupPermissions': 'rwm'}]
            return row

    store, evd, result = _run(tmp_path, RequestedDocker(profile))
    unresolved = requests not in (None, [])
    assert evd.data_origin.value == 'EXAMPLE'
    assert result.verdicts.security == expected
    assert evd.observations['devices'].collected is (not unresolved or known_device)
    assert evd.observations['permission_scan_complete'].value is (not unresolved)
    if unresolved:
        assert 'DEVICE_REQUESTS_UNVERIFIED' in _summary(store, evd)['collection_errors']
    if known_device:
        assert evd.observations['devices'].value == ['/dev/EXAMPLE-unapproved:/dev/EXAMPLE-unapproved:rwm']


@pytest.mark.parametrize('later_error', [False, True])
def test_gpu_does_not_erase_known_forbidden_device_when_later_reader_fails(tmp_path, later_error):
    class KnownDeviceDocker(FakeDocker):
        def inspect_json(self, container, *, timeout):
            result = super().inspect_json(container, timeout=timeout)
            result['HostConfig']['Devices'] = [{'PathOnHost': '/dev/mem', 'PathInContainer': '/dev/mem', 'CgroupPermissions': 'rwm'}]
            return result
        def exec(self, container, argv, *, timeout):
            if later_error and argv[2] == 'cat /proc/1/mounts':
                raise OSError('PRIVATE_EXAMPLE_NOT_EXPORTED')
            return super().exec(container, argv, timeout=timeout)
    store, evd, result = _run(tmp_path, KnownDeviceDocker('hardened'), gpu_count=1)
    assert result.verdicts.security == Verdict.FAIL
    assert evd.observations['devices'].collected is True
    assert evd.observations['devices'].value == ['/dev/mem:/dev/mem:rwm']
    assert evd.observations['permission_scan_complete'].value is False
    assert 'declared_devices' not in _summary(store, evd)
