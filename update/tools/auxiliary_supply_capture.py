"""Capture SUP-03 auxiliary Python parser reads in a separate CPU producer Pod."""
import argparse
import json
from pathlib import Path
import subprocess

from guard.collectors import auxiliary_supply_producer as producer


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ('context', 'namespace', 'pod', 'container', 'model-dir', 'template', 'out'):
        parser.add_argument('--' + option, required=True)
    args = parser.parse_args(argv)
    output = Path(args.out)
    if output.exists():
        raise ValueError('AUXILIARY_OUTPUT_EXISTS')
    cmd = ['kubectl', '--context=' + args.context, '--namespace=' + args.namespace, 'exec', args.pod,
           '--container=' + args.container, '--', 'python', '-c', producer.CAPTURE,
           *producer.capture_args(args.model_dir, args.template)]
    done = subprocess.run(cmd, capture_output=True)
    if done.returncode:
        raise RuntimeError('AUXILIARY_CAPTURE_FAILED')
    value = json.loads(done.stdout.decode().splitlines()[-1])
    result = producer.project(value)
    output.write_text(json.dumps(value, indent=1, sort_keys=True) + '\n')
    print(json.dumps({'parser_scope_complete': result['parser_scope_complete'], 'errors': result['errors']}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
