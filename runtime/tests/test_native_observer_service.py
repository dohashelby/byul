"""Persistent product startup, config boundaries and unchanged watcher delegation."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('native_observer_service', ROOT / 'runtime/tools/native_observer_service.py')
service = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(service)


def config():
    document = {
        'schema': service.SCHEMA,
        'kubeconfig': '/etc/native-kubeconfig',
        'producer_source_sha256': hashlib.sha256((ROOT / 'runtime/tools/update_engine_link.py').read_bytes()).hexdigest(),
        'environment': '/owned/ENV.json', 'target': '/owned/target.json',
        'prepared': '/owned/assets/manifest.json', 'records': '/data/gateway_records.jsonl',
        'output': '/feed/native/receipts.json', 'capture_dir': '/data/native-snapshots',
        'reader_gid': 10010, 'gateway_writer_uid': 10000, 'interval': 5,
        'duration': 10, 'max_delay_ms': 1000,
        'required_scopes': ['asgi_transport', 'worker_batch'],
        'node_image_verification': 'local-containerd-v1',
    }
    document['input_sha256'] = {k: hashlib.sha256(k.encode()).hexdigest() for k in ('environment', 'target', 'prepared')}
    return document


def protected(document):
    def read(path, gid, **kwargs):
        if path == '/etc/native.json':
            assert gid == 0
            return json.dumps(document).encode()
        if path == document['kubeconfig']:
            assert gid == 0
            return b'private credential; never printed or hashed'
        key = next(k for k in document['input_sha256'] if document[k] == path)
        assert gid == 10010
        return key.encode()
    return patch.object(service, 'read_protected', side_effect=read)


def test_service_executes_existing_watcher_with_fixed_inputs_and_no_seal():
    doc = config()
    with patch.object(service.os, 'geteuid', return_value=0), protected(doc), patch.object(service.os, 'execve') as execute:
        assert service.main(['run', '--config', '/etc/native.json']) == 0
    executable, argv, environment = execute.call_args.args
    assert executable == sys.executable
    assert argv[:4] == [sys.executable, '-B', str(ROOT / 'runtime/tools/update_engine_link.py'), 'watch']
    assert argv[argv.index('--prepared') + 1] == doc['prepared']
    assert argv[argv.index('--gateway-writer-uid') + 1] == '10000'
    assert argv[argv.index('--required-scopes') + 1:argv.index('--node-image-verification')] == doc['required_scopes']
    assert not any(v in argv for v in ('--once', '--seal', '--packet'))
    assert environment['PYTHONPATH'] == ':'.join(str(ROOT / p) for p in ('runtime', 'runtime/src', 'update/src'))
    assert environment['KUBECONFIG'] == doc['kubeconfig']


@pytest.mark.parametrize('mutation', ['pin', 'environment-pin', 'target-pin', 'prepared-pin', 'relative', 'bool-gid', 'nan', 'scope', 'unknown', 'credential-list'])
def test_bad_deployment_never_executes_or_rebinds_generation(mutation):
    doc = config()
    if mutation == 'pin': doc['producer_source_sha256'] = '0' * 64
    elif mutation.endswith('-pin'): doc['input_sha256'][mutation[:-4]] = '0' * 64
    elif mutation == 'relative': doc['prepared'] = 'assets/manifest.json'
    elif mutation == 'bool-gid': doc['reader_gid'] = True
    elif mutation == 'nan': doc['interval'] = float('nan')
    elif mutation == 'scope': doc['required_scopes'] = ['unknown']
    elif mutation == 'credential-list': doc['kubeconfig'] = '/etc/native:another-kubeconfig'
    else: doc['seal'] = True
    with patch.object(service.os, 'geteuid', return_value=0), protected(doc), patch.object(service.os, 'execve') as execute:
        assert service.main(['run', '--config', '/etc/native.json']) == 2
    execute.assert_not_called()


def test_nonroot_and_unprotected_config_never_execute():
    for uid, error in ((10000, None), (0, PermissionError('protected file rejected'))):
        with patch.object(service.os, 'geteuid', return_value=uid), patch.object(service, 'read_protected', side_effect=error), patch.object(service.os, 'execve') as execute:
            assert service.main(['run', '--config', '/etc/native.json']) == 2
        execute.assert_not_called()


def test_system_service_boot_restart_and_group_boundaries():
    unit = service.unit_text('/etc/serving-runtime/native-observer.json')
    assert 'User=root\nGroup=root\n' in unit
    assert 'WantedBy=multi-user.target' in unit
    assert 'Restart=on-failure' in unit and 'RestartPreventExitStatus=2' in unit
    assert 'KillMode=control-group' in unit and 'UMask=0077' in unit
    assert ' run --config "/etc/serving-runtime/native-observer.json"' in unit
    assert '--seal' not in unit and '--packet' not in unit
    assert 'KUBECONFIG=/etc/serving-runtime/native-kubeconfig' in unit
    assert f'WorkingDirectory={ROOT / "runtime"}\n' in unit
    for path in ('/etc/native%u.json', '/etc/native\n.json', 'relative.json'):
        with pytest.raises(ValueError): service.unit_text(path)


def test_rendered_service_uses_explicit_native_credential_and_refuses_missing_one():
    doc = config()
    with patch.object(service.os, 'geteuid', return_value=0), protected(doc), patch('builtins.print') as output:
        assert service.main(['render-unit', '--config', '/etc/native.json']) == 0
        assert 'KUBECONFIG=/etc/native-kubeconfig' in output.call_args.args[0]
    del doc['kubeconfig']
    with patch.object(service.os, 'geteuid', return_value=0), protected(doc):
        assert service.main(['run', '--config', '/etc/native.json']) == 2


def test_unprotected_native_credential_prevents_service_start():
    doc = config()
    with patch.object(service.os, 'geteuid', return_value=0), protected(doc) as reader, patch.object(service.os, 'execve') as execute:
        original = reader.side_effect
        def protected_read(path, gid, **kwargs):
            if path == doc['kubeconfig']:
                raise PermissionError('credential unavailable')
            return original(path, gid, **kwargs)
        reader.side_effect = protected_read
        assert service.main(['run', '--config', '/etc/native.json']) == 2
        execute.assert_not_called()
