"""Unprivileged preparation tests; the opt-in kernel program is never run."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools import owned_file_access_kernel_check as tool


def test_preparation_pins_reviewed_source_and_never_launches_or_qualifies(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('Preparation must not launch a process'))
    out = tmp_path / 'new-private-canary'
    result = tool.prepare(out)
    assert result == json.loads((out / 'prepared.json').read_bytes())
    assert result['pinned_source_files_verified'] >= 631
    assert result['canary_program_sha256'] == hashlib.sha256((out / 'canary.py').read_bytes()).hexdigest()
    assert result['canary_body_sha256'] == hashlib.sha256(tool.BODY.read_bytes()).hexdigest()
    assert result['run_argv'] == ['/usr/bin/python3.12', '-I', '-S', str(out / 'canary.py')]
    assert out.stat().st_mode & 0o777 == 0o700
    assert (out / 'canary.py').stat().st_mode & 0o777 == 0o600
    assert all(result[k] is False for k in ('engine_executed', 'actual_gpu_source_qualified',
        'producer_registered', 'whole69_complete', 'kernel_probe_executed_by_preparation'))


def test_preparation_refuses_to_overwrite_an_existing_output(tmp_path):
    (tmp_path / 'keep').write_bytes(b'original')
    with pytest.raises(FileExistsError): tool.prepare(tmp_path)
    assert (tmp_path / 'keep').read_bytes() == b'original'
    assert sorted(p.name for p in tmp_path.iterdir()) == ['keep']


def test_body_tampering_is_refused_before_creating_output(tmp_path, monkeypatch):
    body = tmp_path / 'tampered-body'; body.write_bytes(tool.BODY.read_bytes() + b'\n')
    monkeypatch.setattr(tool, 'BODY', body)
    with pytest.raises(ValueError, match='FILE_ACCESS_KERNEL_CANARY_SOURCE_UNREVIEWED'):
        tool.prepare(tmp_path / 'refused')
    assert not (tmp_path / 'refused').exists()


@pytest.mark.parametrize('duplicate', [False, True])
def test_unique_current_review_receipt_is_required(tmp_path, monkeypatch, duplicate):
    fingerprint = hashlib.sha256((tool.ROOT / 'release/reviewed-source.json').read_bytes()).hexdigest()
    read = Path.read_bytes
    def altered(path):
        raw = read(path)
        if path == tool.ROOT / 'tools/reviewed_manifest_receipts.json':
            value = json.loads(raw)
            rows = [r for r in value['receipts'] if r['manifest_sha256'] == fingerprint]
            assert len(rows) == 1
            value['receipts'] = [r for r in value['receipts'] if r['manifest_sha256'] != fingerprint]
            if duplicate: value['receipts'] += rows * 2
            return json.dumps(value).encode()
        return raw
    monkeypatch.setattr(Path, 'read_bytes', altered)
    with pytest.raises(ValueError, match='FILE_ACCESS_KERNEL_REVIEW_RECEIPT_REQUIRED'):
        tool.prepare(tmp_path / 'refused')
    assert not (tmp_path / 'refused').exists()


def test_unrecognized_standalone_program_is_not_executed(tmp_path, monkeypatch):
    monkeypatch.setattr(tool.file_access_source, 'source', lambda: 'print("EXAMPLE-invalid-program")')
    with pytest.raises(ValueError, match='FILE_ACCESS_KERNEL_PROGRAM_UNAVAILABLE'):
        tool.prepare(tmp_path / 'refused')
    assert not (tmp_path / 'refused').exists()


@pytest.mark.parametrize('invocation', ['script', 'module'])
def test_actual_cli_prepares_without_running_the_privileged_program(tmp_path, invocation):
    out = tmp_path / 'prepared-by-cli'
    argv = [sys.executable, str(Path(tool.__file__).resolve())] if invocation == 'script' else [
        sys.executable, '-m', 'tools.owned_file_access_kernel_check']
    child = subprocess.run([*argv, '--output', str(out)], cwd=tool.ROOT, capture_output=True)
    assert child.returncode == 0, child.stderr.decode()
    result = json.loads(child.stdout)
    assert result['kernel_probe_executed_by_preparation'] is False
    assert result['actual_gpu_source_qualified'] is False
    assert (out / 'canary.py').is_file() and not (out / 'result.json').exists()
