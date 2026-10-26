"""REQ-02 생산자 인계용 실제 파일 캡처. 엔진 기동·판정·EVD/RES 생성은 하지 않는다.

기존 합성 분석기와 별개이며 소유 컨테이너/파일/전송 해시만 확인한다.
생산자 신뢰·인가 의미·probe ledger 연결은 미검증이므로 Guard 상태는 UNKNOWN이다.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import yaml

from guard.collectors.route_observations import capture, load_inputs, validate_output
from guard.runtime.docker import Docker
from guard.executors.owned_capture import SourceCaptureError


REPO = Path(__file__).resolve().parents[2]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", required=True, type=Path)
    parser.add_argument("--target", required=True, type=Path)
    parser.add_argument("--container", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--path", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--execute", action="store_true", help="owned read-only capture, never engine startup")
    args = parser.parse_args(argv)
    try:
        source = {"container": args.container, "image": args.image, "path": args.path}
        env, target = load_inputs(args.env, args.target, source)
        validate_output(args.output, REPO)
        if not args.execute:
            print(json.dumps({"engine_started": False, "capture_executed": False, "ready_to_execute": False,
                              "product_connected": False, "security": "UNKNOWN",
                              "pending": ["explicit_owned_source", "producer_contract", "read_only_capture_authorization"]}))
            return 0
        result = capture(Docker(target["run_id"]), target, env, container=args.container,
                         image=args.image, path=args.path)
        # Parent must already exist: do not turn a capture command into directory management.
        with args.output.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError, SourceCaptureError):
        print("NATIVE_ROUTE_CAPTURE_FAILED")
        return 2
    print(json.dumps({"captured": True, "engine_started": False, "product_connected": False, "security": "UNKNOWN"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
