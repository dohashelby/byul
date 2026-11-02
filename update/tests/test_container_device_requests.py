"""EXAMPLE regression for GPU DeviceRequests omitted on the owned L4 host."""
from copy import deepcopy

import pytest

from guard.models import Verdict
from tests.test_container_config import FakeDocker, _run, _summary


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
