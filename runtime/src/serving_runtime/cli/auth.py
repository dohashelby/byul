"""CLI registration and execution for auth."""

import sys


def add_parser(subparsers):
    commands = subparsers
    auth_parser = commands.add_parser("auth", help="시험용 인증 설정 도구")
    auth_commands = auth_parser.add_subparsers(dest="auth_command")
    auth_commands.add_parser(
        "hash-token",
        help="표준 입력의 토큰을 인증 설정용 SHA-256으로 변환(토큰은 출력하지 않음)",
    )
    return auth_parser


def run(args, parser) -> int:
    auth_parser = parser
    if args.command == "auth" and args.auth_command == "hash-token":
        from getpass import getpass

        from serving_runtime.gateway.auth import token_digest

        try:
            token = getpass("token: ") if sys.stdin.isatty() else sys.stdin.readline()
            print(token_digest(token.rstrip("\r\n")))
        except (EOFError, ValueError) as error:
            print(f"토큰 변환 실패: {error}", file=sys.stderr)
            return 1
        return 0
    if args.command == "auth":
        auth_parser.print_help()
        return 0
