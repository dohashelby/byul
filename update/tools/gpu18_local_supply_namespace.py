"""One unprivileged namespace attempt; only the unchanged owned PLT-02 probe."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

TOOL_SHA256 = '673a895c683362f48c81959e3c3cb0e6bd89ac18fc73c5ca644daa79ff97ff3b'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def identity():
    return {'pid': os.getpid(), 'uid': os.getuid(), 'euid': os.geteuid(), 'gid': os.getgid(),
            'uid_map': Path('/proc/self/uid_map').read_text(),
            'gid_map': Path('/proc/self/gid_map').read_text(),
            'namespaces': {key: os.readlink('/proc/self/ns/' + key) for key in ('user', 'pid', 'mnt')},
            'python': sys.executable, 'python_version': sys.version}


def save(path, data):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--inside', action='store_true')
    args = parser.parse_args()
    tool = Path(__file__).with_name('gpu18_local_supply.py').resolve(strict=True)
    if sha(tool) != TOOL_SHA256:
        raise ValueError('Unchanged PLT-02 tool required')
    if args.inside:
        before = json.loads((args.output / 'host-before.json').read_bytes())
        observed = identity()
        save(args.output / 'namespace-identity.json', observed)
        mappings = [list(map(int, row.split())) for row in observed['uid_map'].splitlines()]
        if (observed['uid'] != 0 or mappings != [[0, before['uid'], 1]]
                or any(observed['namespaces'][key] == before['namespaces'][key] for key in ('user', 'pid', 'mnt'))):
            raise ValueError('Expected isolated mapped-self namespace not established; do not run probe')
        argv = [sys.executable, '-I', '-B', str(tool), '--source-root', str(args.source_root),
                '--output', str(args.output / 'probe'), '--checks', 'PLT-02']
        result = subprocess.run(argv, capture_output=True, timeout=25, check=False)
        (args.output / 'probe.stdout').write_bytes(result.stdout)
        (args.output / 'probe.stderr').write_bytes(result.stderr)
        save(args.output / 'probe-process.json', {'argv': argv, 'returncode': result.returncode,
                                                'tool_sha256': sha(tool)})
        return result.returncode

    args.output.mkdir(parents=True, exist_ok=False)
    before = identity()
    save(args.output / 'host-before.json', before)
    if before['uid'] != 1000 or before['euid'] != 1000:
        raise ValueError('Existing host UID1000 required; no host root/sudo fallback')
    unshare = shutil.which('unshare')
    if unshare is None:
        save(args.output / 'namespace-attempt.json', {'status': 'unshare_not_installed', 'attempts': 0,
                                                     'probe_executed': False})
        return 1
    argv = [unshare, '--user', '--map-root-user', '--pid', '--fork', '--mount-proc',
            sys.executable, '-I', '-B', str(Path(__file__).resolve()), '--inside',
            '--source-root', str(args.source_root), '--output', str(args.output)]
    started = datetime.now(timezone.utc).isoformat()
    result = subprocess.run(argv, capture_output=True, timeout=35, check=False)
    (args.output / 'namespace.stdout').write_bytes(result.stdout)
    (args.output / 'namespace.stderr').write_bytes(result.stderr)
    after = identity()
    same_host_ns = before['namespaces'] == after['namespaces']
    if sha(tool) != TOOL_SHA256 or not same_host_ns:
        raise ValueError('Source or host namespace identity changed')
    packet = {'schema_version': 1, 'input_origin': 'synthetic', 'official': False, 'formal_data_origin': 'EXAMPLE',
              'execution_kind': 'actual_local_component', 'check_id': 'PLT-02',
              'started_at_UTC': started, 'finished_at_UTC': datetime.now(timezone.utc).isoformat(),
              'argv': argv, 'actor': before, 'host_after': after, 'host_namespaces_unchanged': same_host_ns,
              'unshare_source': {'path': unshare, 'sha256': sha(Path(unshare))},
              'unchanged_probe_tool': {'path': str(tool), 'sha256': TOOL_SHA256},
              'namespace_attempts': 1, 'returncode': result.returncode,
              'stdout': result.stdout.decode('utf-8', 'replace'), 'stdout_sha256': hashlib.sha256(result.stdout).hexdigest(),
              'stderr': result.stderr.decode('utf-8', 'replace'), 'stderr_sha256': hashlib.sha256(result.stderr).hexdigest(),
              'namespace_created': (args.output / 'namespace-identity.json').exists(),
              'probe_executed': (args.output / 'probe-process.json').exists(),
              'prior_host_failure_preserved': True, 'source_assert_host_permission_modified': False,
              'sudo_root_host_sysctl_AppArmor_install_or_bypass_operations': 0,
              'AWS_GPU_Docker_engine_or_other10_component_executions': 0,
              'scope_limitations': ['A mapped-root namespace is not host root; exact uid_map is required if created.',
                                    'Support refusal is preserved; no retry with fewer flags or forced permission.',
                                    'Owned synthetic socket is never a vLLM worker IPC inventory.']}
    save(args.output / 'namespace-attempt.json', packet)
    print(json.dumps({'receipt': str(args.output / 'namespace-attempt.json'),
                      'sha256': sha(args.output / 'namespace-attempt.json'),
                      'namespace_created': packet['namespace_created'], 'probe_executed': packet['probe_executed'],
                      'returncode': result.returncode}))
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
