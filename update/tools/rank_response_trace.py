"""Prepare or verify separate source-response captures; never launch an engine."""
import argparse
import json
from pathlib import Path

from guard.adapters import vllm_rank_response_capture as capture


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    prepare = commands.add_parser('prepare')
    prepare.add_argument('--output', type=Path, required=True)
    prepare.add_argument('--rank-prepared', type=Path, required=True)
    verify = commands.add_parser('verify')
    verify.add_argument('--prepared', type=Path, required=True)
    verify.add_argument('--capture', type=Path, required=True)
    verify.add_argument('--expected-uid', type=int)
    args = parser.parse_args()
    if args.command == 'prepare':
        result = capture.prepare(args.output, rank_prepared=args.rank_prepared,
                                 repository=Path(__file__).resolve().parents[2])
    else:
        result = capture.verify_capture(args.prepared, args.capture, expected_uid=args.expected_uid)
        result.pop('rows')
    print(json.dumps(result, ensure_ascii=True))


if __name__ == '__main__':
    main()
