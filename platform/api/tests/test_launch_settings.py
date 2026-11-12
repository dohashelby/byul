"""Environment 0.2/0.1 compatibility and approved settings transport."""
import json

import pytest

from byul_platform.routes.bundle_export import _server_values
from byul_platform.sources.runtime import comparisons
from byul_platform.sources.checklist_data import CHECKS
from conftest import EXAMPLES


def document():
    return json.loads((EXAMPLES / 'environment.json').read_text())


def test_legacy_environment_has_no_settings(contracts):
    legacy = document()
    legacy['schema_version'] = 'byul-environment/0.1'
    legacy['deployment'].pop('launch_settings')
    assert not contracts.errors('environment', legacy)
    assert 'launch_settings' not in _server_values(legacy)['target']
    legacy['deployment']['launch_settings'] = {'trust_remote_code': True}
    assert contracts.errors('environment', legacy)


@pytest.mark.parametrize('settings', [None, {}, {'trust_remote_code': None}, {'trust_request_mm_kwargs': False},
                                       {'revision': 'a' * 128}, {'enable_multimodal': True}])
def test_settings_validation_and_transport(contracts, settings):
    doc = document()
    doc['deployment']['launch_settings'] = settings
    assert not contracts.errors('environment', doc)
    assert _server_values(doc)['target']['launch_settings'] == settings


@pytest.mark.parametrize('settings', [{'secret': 'omitted'}, {'revision': 'a' * 129},
                                       {'trust_remote_code': 'false'}, {'enable_multimodal': 0}])
def test_settings_reject_invalid_values(contracts, settings):
    doc = document()
    doc['deployment']['launch_settings'] = settings
    assert contracts.errors('environment', doc)


@pytest.mark.parametrize('version', ['0.1', '0.2'])
def test_register_read_validate_environment(store_client, version):
    doc = document()
    doc['schema_version'] = 'byul-environment/' + version
    if version == '0.1':
        doc['deployment'].pop('launch_settings')
    result = store_client.post('/api/environments', json=doc)
    assert result.status_code == 201, result.text
    saved = store_client.get(f"/api/environments/{doc['environment_id']}/revisions/1").json()
    assert saved['document']['schema_version'] == doc['schema_version']
    assert saved['document']['deployment'] == doc['deployment']


def test_platform_preserves_launch_comparison_source_and_unknown():
    rows = [{'field': 'launch_settings.trust_remote_code', 'expected': False, 'observed': None,
             'source': 'kubernetes_spec', 'state': 'unknown'}]
    assert comparisons({'rows': rows}, {})['baseline']['rows'][0]['source'] == 'kubernetes_spec'
    assert comparisons({'rows': rows}, {})['baseline']['rows'][0]['state'] == 'unknown'


def test_d7_checklist_metadata():
    checks = {row['framework_id']: row for row in CHECKS}
    assert 'CHK-SUP-002' not in checks
    for check in ['CHK-SUP-008', 'CHK-SUP-014']:
        assert 'launch_settings_drift' in checks[check]['rules']
        assert {'target', 'revalidation'} <= set(checks[check]['sources'])
