"""REQ-02 생산자 인계용 실제 파일 캡처. 엔진 기동·판정·EVD/RES 생성은 하지 않는다.

기존 합성 분석기와 별개이며 소유 컨테이너/파일/전송 해시만 확인한다.
생산자 신뢰·인가 의미·probe ledger 연결은 미검증이므로 Guard 상태는 UNKNOWN이다.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import yaml

from guard.collectors.file import load_environment
from guard.runtime.docker import Docker
from guard.executors.native_observations import validate_capture
from guard.executors.owned_capture import read_owned_json


REPO = Path(__file__).resolve().parents[2]


def capture(docker, target, env, *, container, image, path):
    packet, provenance = read_owned_json(docker, target, container=container, image=image, path=path)
    summary = validate_capture(packet, kind="route", env_ref=env.ref().model_dump(mode="json"),
                               run_id=target["run_id"], image=image)
    return {"schema": "guard-route-native-attachment/1", "capture": provenance, "record": packet,
            "summary": summary, "product_connected": False, "data_origin": "DECLARED",
            "origin_detail": "owned file bytes captured; producer assertions not authenticated",
            "environment_ref": env.ref().model_dump(mode="json"), "security": "UNKNOWN"}


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
        env = load_environment(args.env)
        target = json.loads(args.target.read_text(encoding="utf-8"))
        if (type(target) is not dict or type(target.get("run_id")) is not str or not target["run_id"]
                or args.output.exists() or args.output.is_symlink()
                or args.output.resolve().is_relative_to(REPO.resolve())):
            raise ValueError("CAPTURE_INPUT_OR_OUTPUT_UNSUPPORTED")
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
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError):
        print("NATIVE_ROUTE_CAPTURE_FAILED")
        return 2
    print(json.dumps({"captured": True, "engine_started": False, "product_connected": False, "security": "UNKNOWN"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
