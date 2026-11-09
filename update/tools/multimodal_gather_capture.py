"""Capture REQ-09 typed embedding/gather CPU components in an isolated producer Pod."""
import argparse
import json
from pathlib import Path
import subprocess

from guard.executors import multimodal_gather_cpu as producer


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ('context', 'namespace', 'pod', 'container', 'out'):
        parser.add_argument('--' + option, required=True)
    args = parser.parse_args(argv)
    output = Path(args.out)
    if output.exists():
        raise ValueError('MM_GATHER_OUTPUT_EXISTS')
    cmd = ['kubectl', '--context=' + args.context, '--namespace=' + args.namespace, 'exec', args.pod,
           '--container=' + args.container, '--', 'python', '-c', producer.RUNNER, *producer.runner_args()]
    done = subprocess.run(cmd, capture_output=True)
    if done.returncode:
        raise RuntimeError('MM_GATHER_CAPTURE_FAILED')
    value = json.loads(done.stdout.decode().splitlines()[-1])
    output.write_text(json.dumps(value, indent=1, sort_keys=True) + '\n')
    print(json.dumps(producer.project(value)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
