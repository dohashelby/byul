"""Run the actual tiny random MM processor/model CPU components, with no download."""
import argparse
import json
from pathlib import Path
import subprocess

from guard.collectors import multimodal_processor_model as producer
from tools.consumption_links_capture import validate_pod


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ('context', 'namespace', 'pod', 'container', 'out'):
        parser.add_argument('--' + option, required=True)
    args = parser.parse_args(argv); output = Path(args.out)
    if output.exists():
        raise ValueError('MM_PROCESSOR_MODEL_OUTPUT_EXISTS')
    base = ['kubectl', '--context=' + args.context, '--namespace=' + args.namespace]
    pod = subprocess.run(base + ['get', 'pod', args.pod, '-o', 'json'], capture_output=True, check=True)
    binding = validate_pod(json.loads(pod.stdout), container=args.container, model_dir='/models')
    source = producer.producer()
    done = subprocess.run(base + ['exec', args.pod, '--container=' + args.container, '--', 'env', 'HF_HUB_OFFLINE=1',
                                 'TRANSFORMERS_OFFLINE=1', 'python', '-c', producer.RUNNER, *producer.runner_args()], capture_output=True)
    output.with_suffix('.stdout').write_bytes(done.stdout); output.with_suffix('.stderr').write_bytes(done.stderr)
    if done.returncode:
        raise RuntimeError('MM_PROCESSOR_MODEL_CAPTURE_FAILED')
    record = json.loads(done.stdout.decode().splitlines()[-1])
    after = subprocess.run(base + ['get', 'pod', args.pod, '-o', 'json'], capture_output=True)
    stable = after.returncode == 0 and validate_pod(json.loads(after.stdout), container=args.container, model_dir='/models') == binding
    record['source_ended'] = record['source_ended'] and stable
    output.write_text(json.dumps(record, sort_keys=True, indent=1) + '\n')
    if producer.producer() != source:
        raise RuntimeError('MM_PROCESSOR_MODEL_SOURCE_CHANGED_DURING_CAPTURE')
    print(json.dumps(producer.project(record)))
    return 0 if record['source_ended'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
