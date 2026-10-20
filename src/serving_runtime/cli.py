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
        description="Serving Runtime 개발 도구: 도움말·버전 조회와 로컬 파일 정보 확인.",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {version('serving-runtime')}"
    )
    commands = parser.add_subparsers(dest="command")
    file_parser = commands.add_parser("file-info", help="파일 크기와 SHA-256 확인")
    file_parser.add_argument("path", type=Path, help="확인할 일반 파일 경로")
    mvp_parser = commands.add_parser("mvp", help="로컬 Gateway와 모의 엔진 실행")
    mvp_parser.add_argument("--policy", default="examples/mvp/policy.json", metavar="PATH")
    mvp_parser.add_argument("--gateway-port", type=int, default=8080, metavar="N")
    mvp_parser.add_argument("--engine-port", type=int, default=8088, metavar="N")
    mvp_parser.add_argument("--data-dir", metavar="PATH")
    mvp_parser.add_argument("--relay-timeout", type=float, default=5.0, metavar="SECONDS")
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
                "relay_timeout_s": args.relay_timeout,
            }
            if args.data_dir is not None:
                options["data_dir"] = args.data_dir
            run(MVPConfig(**options))
        except (OSError, ValueError) as error:
            print(f"MVP 실행 실패: {error}", file=sys.stderr)
            return 1
        return 0
    parser.print_help()
    return 0
