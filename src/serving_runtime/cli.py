"""Development CLI for local inspection and the serving runtime MVP."""

import argparse
from collections.abc import Sequence
from importlib.metadata import version
from pathlib import Path
import sys

from .file_info import inspect_file


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="serving-runtime",
        description="Serving Runtime 개발 도구: 로컬 파일 확인과 런타임 통제 MVP 실행·시험.",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {version('serving-runtime')}"
    )
    commands = parser.add_subparsers(dest="command")
    file_parser = commands.add_parser("file-info", help="파일 크기와 SHA-256 확인")
    file_parser.add_argument("path", type=Path, help="확인할 일반 파일 경로")
    mvp_parser = commands.add_parser("mvp", help="로컬 Gateway와 선택한 backend 실행")
    mvp_parser.add_argument("--policy", default="examples/mvp/policy.json", metavar="PATH")
    mvp_parser.add_argument("--gateway-port", type=int, default=8080, metavar="N")
    mvp_parser.add_argument("--engine-port", type=int, default=8088, metavar="N")
    mvp_parser.add_argument("--backend", choices=("mock", "vllm"), default="mock")
    mvp_parser.add_argument("--backend-config", metavar="PATH")
    mvp_parser.add_argument("--data-dir", metavar="PATH")
    mvp_parser.add_argument("--relay-timeout", type=float, default=5.0, metavar="SECONDS")
    mvp_parser.add_argument(
        "--stream-idle-timeout", type=float, default=30.0, metavar="SECONDS"
    )
    mvp_parser.add_argument(
        "--request-deadline", type=float, default=300.0, metavar="SECONDS"
    )
    mvp_parser.add_argument("--max-concurrency", type=int, default=2, metavar="N")
    mvp_parser.add_argument("--revocations", metavar="PATH")
    mvp_parser.add_argument("--baseline", metavar="PATH")
    mvp_parser.add_argument("--manifest", metavar="PATH")
    mvp_parser.add_argument("--collector-interval", type=float, default=5.0, metavar="SECONDS")
    mvp_parser.add_argument(
        "--collector-stale-after", type=float, default=15.0, metavar="SECONDS"
    )
    demo_parser = commands.add_parser("mvp-demo", help="실행 중인 MVP 시연 사례 전송")
    demo_parser.add_argument("--gateway", default="http://127.0.0.1:8080", metavar="URL")
    demo_parser.add_argument("--data-dir", metavar="PATH")
    acceptance_parser = commands.add_parser(
        "mvp-acceptance",
        help="격리된 MVP-01~11·S1-01~06·S2-01~06·S3A-01~08 수용 시험 실행",
    )
    acceptance_parser.add_argument("--report-dir", metavar="PATH")
    args = parser.parse_args(argv)
    if args.command == "file-info":
        try:
            info = inspect_file(args.path)
        except (OSError, ValueError) as error:
            print(f"파일 확인 실패: {error}", file=sys.stderr)
            return 1
        print(f"파일: {str(args.path)!r}")
        print(f"크기(bytes): {info.size_bytes}")
        print(f"SHA-256: {info.sha256}")
        return 0
    if args.command == "mvp":
        from .mvp.app import run
        from .mvp.config import MVPConfig

        try:
            options = {
                "policy_path": args.policy,
                "gateway_port": args.gateway_port,
                "engine_port": args.engine_port,
                "backend": args.backend,
                "backend_config_path": args.backend_config,
                "relay_timeout_s": args.relay_timeout,
                "stream_idle_timeout_s": args.stream_idle_timeout,
                "request_deadline_s": args.request_deadline,
                "max_concurrent_requests": args.max_concurrency,
                "revocations_path": args.revocations,
                "baseline_path": args.baseline,
                "manifest_path": args.manifest,
                "collector_interval_s": args.collector_interval,
                "collector_stale_after_s": args.collector_stale_after,
            }
            if args.data_dir is not None:
                options["data_dir"] = args.data_dir
            run(MVPConfig(**options))
        except (OSError, ValueError) as error:
            print(f"MVP 실행 실패: {error}", file=sys.stderr)
            return 1
        return 0
    if args.command == "mvp-demo":
        from .mvp.demo import run_cli

        return run_cli(args.gateway, args.data_dir)
    if args.command == "mvp-acceptance":
        from .mvp.acceptance import run_cli

        return run_cli(args.report_dir)
    parser.print_help()
    return 0
