"""Run pinned REQ-10 CPU components in an isolated producer Pod (EXAMPLE)."""
import argparse
import json
from pathlib import Path
import subprocess

from guard.executors import sampling_consumption_cpu as producer


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ('context', 'namespace', 'pod', 'container', 'model-dir', 'out'):
        parser.add_argument('--' + option, required=True)
    args = parser.parse_args(argv)
    output = Path(args.out)
    if output.exists():
        raise ValueError('SAMPLING_CONSUMPTION_OUTPUT_EXISTS')
    cmd = ['kubectl', '--context=' + args.context, '--namespace=' + args.namespace, 'exec', args.pod,
           '--container=' + args.container, '--', 'python', '-c', producer.RUNNER, *producer.runner_args(args.model_dir)]
    done = subprocess.run(cmd, capture_output=True)
    if done.returncode:
        raise RuntimeError('SAMPLING_CONSUMPTION_CAPTURE_FAILED')
    value = json.loads(done.stdout.decode().splitlines()[-1])
    output.write_text(json.dumps(value, indent=1, sort_keys=True) + '\n')
    result = producer.project(value)
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
