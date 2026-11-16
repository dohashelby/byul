"""CLI registration and execution for management-listener."""

import sys


def add_parser(subparsers):
    commands = subparsers
    management_parser = commands.add_parser(
        "management-listener",
        help="엔진의 허용된 읽기 전용 관리 경로만 중계",
    )
    management_parser.add_argument("--listen-host", default="127.0.0.1", metavar="HOST")
    management_parser.add_argument("--listen-port", type=int, default=8081, metavar="N")
    management_parser.add_argument("--engine-url", required=True, metavar="URL")
    management_parser.add_argument(
        "--allow-path",
        action="append",
        metavar="PATH",
        help="정확히 허용할 경로(반복 가능; 생략 시 /metrics,/health,/v1/models,/version)",
    )
    management_parser.add_argument("--timeout", type=float, default=5.0, metavar="SECONDS")
    management_parser.add_argument("--max-response-bytes", type=int, default=1024 * 1024, metavar="BYTES")
    management_parser.add_argument(
        "--credential-file",
        metavar="PATH",
        help="엔진 Bearer token 파일(backend credential.file과 같은 읽기 규칙)",
    )
    management_parser.add_argument(
        "--record-file",
        metavar="PATH",
        help="최소 접근 JSONL 추가 기록(접근 기록은 항상 stdout에도 출력)",
    )
    management_parser.add_argument("--heartbeat-file", metavar="PATH", help="연속 관리접근 수집기의 writer 상태 파일")
    for field in ("target-ref", "run-id", "environment", "policy-ref"):
        management_parser.add_argument("--" + field, help="관리 접근 감사에 남길 설정 기반 참조")
    return management_parser


def run(args, parser) -> int:
    from serving_runtime.gateway.management_listener import ManagementListenerConfig, run

    try:
        options = {
            "listen_host": args.listen_host,
            "listen_port": args.listen_port,
            "engine_url": args.engine_url,
            "timeout_s": args.timeout,
            "max_response_bytes": args.max_response_bytes,
            "credential_file": args.credential_file,
            "record_path": args.record_file,
            "heartbeat_path": args.heartbeat_file,
            "target_ref": args.target_ref,
            "run_id": args.run_id,
            "environment": args.environment,
            "policy_ref": args.policy_ref,
        }
        if args.allow_path:
            options["allowed_paths"] = tuple(args.allow_path)
        run(ManagementListenerConfig.build(**options))
    except (OSError, ValueError) as error:
        print(f"관리 listener 실행 실패: {error}", file=sys.stderr)
        return 1
    return 0
