"""Health receipt replacement starts private and leaves prior state on failure."""
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from serving_runtime.mvp.observation_feed import ObservationFeed


def feed(root):
    return ObservationFeed(kind='tetragon', path=root/'feed', data_dir=root,
                           data_origin='real', environment=None, run_id=None)


def item():
    return {'identity': {'node': 'node'}, 'observed_at': '2026-11-08T00:00:00+00:00'}


def test_predictable_old_temporary_symlink_is_not_followed(tmp_path):
    target = tmp_path/'unrelated'
    target.write_bytes(b'untouched')
    (tmp_path/'tetragon-health-receipts.json.next').symlink_to(target)
    feed(tmp_path)._save_health_receipt(item())
    assert target.read_bytes() == b'untouched'
    receipt = tmp_path/'tetragon-health-receipts.json'
    assert receipt.stat().st_mode & 0o777 == 0o600
    assert json.loads(receipt.read_text())['nodes']['node'] == item()


def test_receipt_temporary_is_private_from_creation_with_permissive_umask(tmp_path):
    original = os.open
    modes = []
    def checked(path, flags, mode=0o777, **kwargs):
        descriptor = original(path, flags, mode, **kwargs)
        if str(path).endswith('.tmp'):
            modes.append(os.fstat(descriptor).st_mode & 0o777)
        return descriptor
    previous = os.umask(0)
    try:
        with patch('serving_runtime.mvp.observation_feed.os.open', side_effect=checked):
            feed(tmp_path)._save_health_receipt(item())
    finally:
        os.umask(previous)
    assert modes == [0o600]


def test_failed_replace_preserves_prior_receipt_and_cleans_temporary(tmp_path):
    source = feed(tmp_path)
    source._save_health_receipt(item())
    receipt = tmp_path/'tetragon-health-receipts.json'
    original = receipt.read_bytes()
    with patch('serving_runtime.mvp.observation_feed.os.replace', side_effect=OSError('disk')):
        with pytest.raises(OSError):
            source._save_health_receipt({**item(), 'observed_at': '2026-11-08T00:01:00+00:00'})
    assert receipt.read_bytes() == original
    assert list(tmp_path.glob('.*.tmp')) == []


@pytest.mark.parametrize("mode", [0o644, 0o660, 0o777, True, "0640"])
def test_health_receipt_mode_rejects_unapproved_permissions(tmp_path, mode):
    with pytest.raises(ValueError, match="health_receipt_mode"):
        ObservationFeed(kind='tetragon', path=tmp_path/'feed', data_dir=tmp_path,
                        data_origin='real', environment=None, run_id=None,
                        health_receipt_mode=mode)


def test_gateway_shares_only_tetragon_health_receipts_with_configured_status_reader(tmp_path):
    from serving_runtime.mvp.app import GatewayApp
    from serving_runtime.mvp.config import MVPConfig
    from serving_runtime.mvp.policy import load_policy
    policy = Path(__file__).resolve().parents[1]/'examples/mvp/policy.json'
    for configured in (False, True):
        root = tmp_path/str(configured)
        config = MVPConfig(policy_path=str(policy), data_dir=str(root),
            observation_feeds=(("tetragon", str(root/'feed')), ("hubble", str(root/'hubble'))),
            tetragon_state_path=str(root/'state') if configured else None)
        gateway = GatewayApp(config, load_policy(policy), ('127.0.0.1', 1))
        modes = {source.kind: source.health_receipt_mode for source in gateway._observation_feeds}
        assert modes == {"tetragon": 0o640 if configured else 0o600, "hubble": 0o600}
