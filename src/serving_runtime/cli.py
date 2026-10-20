"""Development CLI for local file inspection; runtime controls are not implemented."""

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
    parser.print_help()
    return 0
