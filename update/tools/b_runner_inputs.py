"""REQ-08/09/10 입력 준비 CLI. 구현은 설치 가능한 guard 패키지에 둔다."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from guard.executors.runner_inputs import (
    CHECKS, INPUT_SCHEMA, KIND, MAX_WRAPPER_BYTES, OUTPUT_SCHEMA, SOURCE_NAMES,
    WRAPPER_SCHEMA, extract_record, prepare,
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--inputs-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        receipt = prepare(args.inputs, args.inputs_sha256, args.output)
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(json.dumps({"schema": OUTPUT_SCHEMA, "error": {
                          "type": getattr(error, "original_error_type", type(error).__name__), "message": str(error)},
                          "runtime_link_complete": False, "gpu_ready": False}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps({"schema": OUTPUT_SCHEMA, "check_id": receipt["check_id"], "output": str(args.output.absolute()),
                      "consumer_contract_complete": receipt["consumer_projection"]["complete"],
                      "runtime_link_complete": False, "gpu_ready": False}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
