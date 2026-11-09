"""Observe the existing IPC probe's caught error; no substitution/privilege change."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    target = args.source_root.resolve(strict=True) / 'update/src/guard/executors/ipc_probe.py'
    expected = 'f096e9d95aed61315b4d129369cd273786fa9dc106ac264d48c670e004269ca2'
    if hashlib.sha256(target.read_bytes()).hexdigest() != expected:
        raise ValueError('Different probe source: diagnostic target must be reviewed again')
    args.output.mkdir(parents=True, exist_ok=False)
    spec = importlib.util.spec_from_file_location('gpu18_original_ipc_probe', target)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    events = []

    def observe(frame, event, arg):
        if frame.f_code.co_filename != str(target) or frame.f_code.co_name != 'inspect_paths':
            return None
        if event == 'exception' and len(events) < 32:
            kind, error, _ = arg
            events.append({'line': frame.f_lineno, 'exception_type': kind.__name__,
                           'message': str(error), 'errno': getattr(error, 'errno', None),
                           'source_function': frame.f_code.co_name})
        return observe

    with tempfile.TemporaryDirectory(prefix='gpu18-ipc-diag-') as temp:
        endpoint = Path(temp) / 'normal.sock'
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(endpoint))
            listener.listen(1)
            owned_stat = module._stat_socket(str(endpoint))
            try:
                sys.settrace(observe)
                inspected = module.inspect_paths([str(endpoint)])
            finally:
                sys.settrace(None)
            connected = module.connect_paths([str(endpoint)], 0.5)
        endpoint.unlink()
        absent = not endpoint.exists()
    if hashlib.sha256(target.read_bytes()).hexdigest() != expected:
        raise ValueError('Probe source changed during diagnostic')
    result = {'schema_version': 1, 'execution_kind': 'actual_local_component', 'input_origin': 'synthetic',
              'official': False, 'formal_data_origin': 'EXAMPLE', 'check_id': 'PLT-02',
              'recorded_at_UTC': datetime.now(timezone.utc).isoformat(),
              'command_or_api': 'same original inspect_paths with exception observation via sys.settrace; function not replaced',
              'target_source': {'path': str(target), 'sha256': expected, 'version': 'guard-unix-ipc-probe/1'},
              'input_sha256': hashlib.sha256(b'owned normal UDS, no payload, same UID').hexdigest(),
              'runtime': {'python': sys.version, 'executable': sys.executable, 'uid': os.geteuid(), 'pid': os.getpid()},
              'observations': {'actual_caught_exceptions': events, 'owned_stat': owned_stat,
                               'metadata': inspected, 'connection': connected, 'owned_path_removed': absent},
              'scope_limitations': ['New same-environment reproduction; not the original v2 hidden exception.',
                                    'sys.settrace observes exceptions only; no monkeypatch/AST extraction or source change.',
                                    'Own process listener is not vLLM IPC; no UID switch, bypass, payload or network service.'],
              'remaining_real_integration': ['Supported isolated PID/owner metadata scope or explicit selected-owner contract.',
                                              'Do not skip inaccessible owner candidates and claim complete inventory.',
                                              'Actual engine IPC subject/inventory/consumer remains unverified.'],
              'requires_gpu_for_remaining': False, 'original_v2_assertion_failure_preserved': True,
              'AWS_GPU_Docker_model_download_external_network_operations': 0}
    path = args.output / 'diagnostic.json'
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                      'caught': events, 'original_probe_complete': inspected['rows'][0]['complete']}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
