"""Capture actual CPU engine consumption in an explicitly isolated producer Pod.

Requires an already running sleep-only Pod, no service-account token, an image
digest and read-only local models. Starts no cloud resource and downloads none.
Original stdout/stderr/data are retained even if the engine or projection fails.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

from guard.collectors import consumption_links as default_producer, engine_callbacks


def validate_pod(value, *, container, model_dir):
    if type(model_dir) is not str or not model_dir.startswith('/') or os.path.normpath(model_dir) != model_dir or '..' in Path(model_dir).parts:
        raise ValueError('CONSUMPTION_LINK_MODEL_PATH_INVALID')
    spec = value.get('spec') or {}; status = value.get('status') or {}
    selected = [c for c in spec.get('containers', []) if c.get('name') == container]
    states = [c for c in status.get('containerStatuses', []) if c.get('name') == container]
    if (spec.get('automountServiceAccountToken') is not False or len(selected) != 1 or len(states) != 1
            or status.get('phase') != 'Running' or 'running' not in (states[0].get('state') or {})):
        raise ValueError('CONSUMPTION_LINK_REQUIRES_ISOLATED_POD')
    selected, state = selected[0], states[0]
    mounts = [m for m in selected.get('volumeMounts', []) if type(m.get('mountPath')) is str and m['mountPath'].startswith('/')]
    def covers(parent, child):
        return child == parent or child.startswith(parent.rstrip('/') + '/')
    if (selected.get('command') != ['sleep', '3600'] or selected.get('args')
            or not selected.get('image', '').endswith('@sha256:045fb648a61cfcd32d4b9ca5d0c391013afadae0178d2e23ac292597f2f64036') or selected.get('imagePullPolicy') != 'Never'
            or not any(covers(m['mountPath'], model_dir) and m.get('readOnly') is True for m in mounts)
            or any(m.get('readOnly') is not True and (covers(model_dir, m['mountPath']) or covers(m['mountPath'], model_dir)) for m in mounts)):
        raise ValueError('CONSUMPTION_LINK_REQUIRES_ISOLATED_POD')
    return {'pod_uid': value['metadata']['uid'], 'container_id': state['containerID'],
            'restart_count': state['restartCount'], 'image': selected['image']}


def capture(*, context, namespace, pod, container, model_dir, output, producer_module=None):
    producer = producer_module or default_producer
    output = Path(output)
    assets, data = output.with_suffix('.assets'), output.with_suffix('.data')
    if output.exists() or assets.exists() or data.exists():
        raise ValueError('CONSUMPTION_LINK_OUTPUT_EXISTS')
    base = ['kubectl', '--context=' + context, '--namespace=' + namespace]
    def run(argv):
        return subprocess.run(base + argv, capture_output=True)
    before = run(['get', 'pod', pod, '-o', 'json'])
    if before.returncode:
        raise RuntimeError('CONSUMPTION_LINK_POD_UNAVAILABLE')
    binding = validate_pod(json.loads(before.stdout), container=container, model_dir=model_dir)
    empty = run(['exec', pod, '--container=' + container, '--', 'test', '!', '-e', '/guard-lifecycle/engine-callbacks'])
    if empty.returncode:
        raise ValueError('CONSUMPTION_LINK_PREPARED_DIRECTORY_EXISTS')
    manifest = engine_callbacks.prepare_assets(assets, execution_links=True, supply_roots=[model_dir, *getattr(producer, 'SYNTHETIC_ROOTS', ())])
    source = producer.producer()
    copied = run(['cp', str(assets), pod + ':/guard-lifecycle/engine-callbacks', '-c', container])
    if copied.returncode:
        raise RuntimeError('CONSUMPTION_LINK_PREPARE_FAILED')
    runner = getattr(producer, 'CPU_RUNNER', None) or producer.RUNNER
    arguments = producer.runner_args(model_dir) if hasattr(producer, 'runner_args') else [json.dumps(model_dir)]
    command = ['exec', pod, '--container=' + container, '--', 'env', 'PYTHONPATH=/guard-lifecycle/engine-callbacks',
               'HF_HUB_OFFLINE=1', 'TRANSFORMERS_OFFLINE=1', 'OMP_NUM_THREADS=2', 'python', '-c', runner, *arguments]
    done = run(command)
    output.with_suffix('.stdout').write_bytes(done.stdout)
    output.with_suffix('.stderr').write_bytes(done.stderr)
    # Always copy raw records before inspecting outcomes or running the consumer.
    copied = run(['cp', pod + ':/guard-lifecycle/data/' + manifest['session'], str(data), '-c', container])
    rows = []
    if data.exists():
        for path in sorted(data.rglob('callbacks-*.jsonl')):
            for line in path.read_text().splitlines():
                row = json.loads(line)
                if row.get('schema') in (producer.callback.SCHEMA, producer.auxiliary.SCHEMA) or row.get('phase') == 'GAP':
                    rows.append(row)
    after = run(['get', 'pod', pod, '-o', 'json'])
    stable = after.returncode == 0 and validate_pod(json.loads(after.stdout), container=container, model_dir=model_dir) == binding
    outputs = [line.split('=', 1)[1] for line in done.stdout.decode().splitlines() if line.startswith('GUARD_CAPTURE_RESULT=')]
    record = {'schema': producer.SCHEMA, 'producer': source, 'data_origin': 'EXAMPLE', 'session': manifest['session'],
              'pod': binding, 'prepared_sha256': hashlib.sha256((assets / 'manifest.json').read_bytes()).hexdigest(),
              'source_ended': done.returncode == 0 and copied.returncode == 0 and stable,
              'rows': rows, 'outputs': json.loads(outputs[0]) if len(outputs) == 1 else []}
    output.write_text(json.dumps(record, sort_keys=True, indent=1) + '\n')
    if engine_callbacks.load_prepared(assets) != manifest or producer.producer() != source:
        raise ValueError('CONSUMPTION_LINK_SOURCE_CHANGED_DURING_CAPTURE')
    result = producer.project(record)
    output.with_suffix('.projection.json').write_text(json.dumps(result, sort_keys=True, indent=1) + '\n')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ('context', 'namespace', 'pod', 'container', 'model-dir', 'out'):
        parser.add_argument('--' + option, required=True)
    args = parser.parse_args(argv)
    result = capture(context=args.context, namespace=args.namespace, pod=args.pod, container=args.container, model_dir=args.model_dir, output=args.out)
    print(json.dumps({'scenario_complete': result['scenario_complete'], 'selected_request_chains_complete': result['selected_request_chains_complete'],
                      'requests': len(result['requests']), 'steps': sum(len(r['steps']) for r in result['requests']), 'diagnostic_codes': result['diagnostic_codes']}))
    return 0 if result['source_ended'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
