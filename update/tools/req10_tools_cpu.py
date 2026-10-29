"""Collect pinned reference CPU observations without engine/network/model loading."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from guard.executors.structured_tools_cpu import capture_reference, project
from tools.b1_integration_cpu import verify_tool_checkout


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--model', default='fixture-model')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    verify_tool_checkout(Path(__file__).resolve().parents[2])
    if args.output.exists():
        raise SystemExit('Output exists; choose a fresh path')
    record = capture_reference(args.source_root, model=args.model)
    record['observations'] = project(record['rows'])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(record, stream, sort_keys=True, indent=2)
    print('reference CPU collection completed; installed target unverified')


if __name__ == '__main__':
    main()
