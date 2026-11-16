"""CLI registration and execution for revalidation."""

import sys


def add_parser(subparsers):
    commands = subparsers
    revalidation_parser = commands.add_parser("revalidation", help="재검증 요청 조회 연동 도구")
    revalidation_commands = revalidation_parser.add_subparsers(dest="revalidation_command")
    revalidation_export_parser = revalidation_commands.add_parser(
        "export", help="열린 재검증 요청을 새 JSON 파일로 내보내기"
    )
    revalidation_export_parser.add_argument("--data-dir", required=True, metavar="DIR")
    revalidation_export_parser.add_argument("--out", required=True, metavar="FILE")
    return revalidation_parser


def run(args, parser) -> int:
    revalidation_parser = parser
    if args.command == "revalidation" and args.revalidation_command == "export":
        from serving_runtime.policy.revalidation import export_requests

        try:
            document = export_requests(args.data_dir, args.out)
        except (OSError, ValueError) as error:
            print(f"재검증 요청 내보내기 실패: {error}", file=sys.stderr)
            return 1
        print(f"열린 재검증 요청 {document['open_count']}건을 {args.out}에 썼습니다.")
        print(document["format_status"])
        return 0
    if args.command == "revalidation":
        revalidation_parser.print_help()
        return 0
