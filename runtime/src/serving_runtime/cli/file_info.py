"""CLI registration and execution for file-info."""

from pathlib import Path
import sys
from serving_runtime.common.file_info import inspect_file


def add_parser(subparsers):
    commands = subparsers
    file_parser = commands.add_parser("file-info", help="파일 크기와 SHA-256 확인")
    file_parser.add_argument("path", type=Path, help="확인할 일반 파일 경로")
    return file_parser


def run(args, parser) -> int:
    try:
        info = inspect_file(args.path)
    except (OSError, ValueError) as error:
        print(f"파일 확인 실패: {error}", file=sys.stderr)
        return 1
    print(f"파일: {str(args.path)!r}")
    print(f"크기(bytes): {info.size_bytes}")
    print(f"SHA-256: {info.sha256}")
    return 0
