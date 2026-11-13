"""CLI registration and execution for assets."""

import json
import os
from pathlib import Path
import sys


def add_parser(subparsers):
    commands = subparsers
    assets_parser = commands.add_parser("assets", help="모델 파일 승인 목록 도구")
    assets_commands = assets_parser.add_subparsers(dest="assets_command")
    snapshot_parser = assets_commands.add_parser("snapshot", help="현재 파일로 목록 초안 생성(관측값, 승인은 사람이)")
    snapshot_parser.add_argument("--root", required=True, metavar="DIR")
    snapshot_parser.add_argument("--out", required=True, metavar="PATH")
    return assets_parser


def run(args, parser) -> int:
    assets_parser = parser
    if args.command == "assets" and args.assets_command == "snapshot":
        from serving_runtime.target.assets import snapshot

        out = Path(args.out)
        try:
            document = snapshot(args.root)
            encoded = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            descriptor = os.open(
                out,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
            )
            try:
                with os.fdopen(descriptor, "wb") as output:
                    descriptor = -1
                    output.write(encoded)
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
        except (OSError, ValueError) as error:
            print(f"목록 생성 실패: {error}", file=sys.stderr)
            return 1
        print(f"{len(document['files'])}개 파일 목록을 {out}에 썼습니다.")
        print("이 목록은 현재 파일의 관측값입니다. 승인 목록으로 쓰기 전에 사람이 검토해야 합니다.")
        return 0
    if args.command == "assets":
        assets_parser.print_help()
        return 0
