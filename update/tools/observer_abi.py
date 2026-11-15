"""Measure/verify observer ABI pins with a bare CPython interpreter; no engine imports."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--measure', action='store_true', help='print measurements; never edits or approves the catalog')
    args = parser.parse_args(argv)
    path = ROOT / 'src/guard/adapters/vllm_observer_abi.py'
    spec = importlib.util.spec_from_file_location('_standalone_observer_abi', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.measure(path.parent) if args.measure else module.verify(path.parent)
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == '__main__':
    main()
