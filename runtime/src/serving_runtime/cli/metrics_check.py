"""CLI registration and execution for metrics-check."""

import hashlib
import json
from pathlib import Path
import sys


def add_parser(subparsers):
    commands = subparsers
    metrics_parser = commands.add_parser("metrics-check", help="저장한 /metrics 응답과 backend 지표 대응표 대조")
    metrics_parser.add_argument("--backend-config", required=True, metavar="PATH")
    metrics_parser.add_argument("--input", required=True, metavar="FILE")
    metrics_parser.add_argument("--json", action="store_true", help="JSON으로 출력")
    return metrics_parser


def run(args, parser) -> int:
    from serving_runtime.gateway.backend import (
        load_backend_config,
        parse_prometheus_metrics,
        unmapped_metric_families,
    )

    try:
        backend = load_backend_config(args.backend_config, environ={})
        payload = Path(args.input).read_bytes()
        fields = parse_prometheus_metrics(payload, backend.metrics_map)
    except (OSError, ValueError) as error:
        print(f"지표 대응표 대조 실패: {error}", file=sys.stderr)
        return 1
    unmapped = unmapped_metric_families(payload, backend.metrics_map)
    report = {
        "schema": "runtime-metric-map-check/1.0",
        "backend_kind": backend.kind,
        "input": str(args.input),
        "input_sha256": hashlib.sha256(payload).hexdigest(),
        "fields": {
            name: {
                key: item[key]
                for key in (
                    "metric",
                    "type",
                    "unit",
                    "state",
                    "declared_type",
                    "type_check",
                    "help",
                    "label_keys",
                )
            }
            | {"sample_count": len(item["samples"])}
            for name, item in fields.items()
        },
        "unmapped_families": unmapped,
        "note": "verified stays false in the backend config; this report is review evidence only",
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"backend: {backend.kind} · 입력 SHA-256 {report['input_sha256'][:12]}…")
        for name, item in report["fields"].items():
            print(
                f"- {name}: {item['metric']} · {item['state']} · 선언 형식 "
                f"{item['declared_type'] or '없음'}({item['type_check']}) · 표본 {item['sample_count']}"
            )
        print(f"대응표에 없는 지표 {len(unmapped)}개")
    ok = all(item["state"] == "ok" for item in report["fields"].values())
    return 0 if ok else 3
