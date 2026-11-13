"""Development CLI for local inspection and the serving runtime MVP."""

import argparse
from collections.abc import Sequence
from importlib.metadata import version


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="serving-runtime",
        description="Serving Runtime 개발 도구: 로컬 파일 확인과 런타임 통제 MVP 실행·시험.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {version('serving-runtime')}")
    commands = parser.add_subparsers(dest="command")
    from . import agent as _agent_command

    agent_parser = _agent_command.add_parser(commands)
    from . import file_info as _file_info_command

    file_parser = _file_info_command.add_parser(commands)
    from . import bundle as _bundle_command

    bundle_parser = _bundle_command.add_parser(commands)
    from . import mvp as _mvp_command

    mvp_parser = _mvp_command.add_parser(commands)
    from . import management_listener as _management_listener_command

    management_parser = _management_listener_command.add_parser(commands)
    from . import demo as _demo_command

    _demo_command.add_parser(commands)
    from . import assets as _assets_command

    assets_parser = _assets_command.add_parser(commands)
    from . import revalidation as _revalidation_command

    revalidation_parser = _revalidation_command.add_parser(commands)
    from . import auth as _auth_command

    auth_parser = _auth_command.add_parser(commands)
    from . import metrics_check as _metrics_check_command

    metrics_parser = _metrics_check_command.add_parser(commands)
    from . import enforce as _enforce_command

    enforce_parser = _enforce_command.add_parser(commands)
    args = parser.parse_args(argv)
    if args.command == "agent":
        return _agent_command.run(args, parser)
    if args.command == "file-info":
        return _file_info_command.run(args, parser)
    if args.command == "bundle":
        return _bundle_command.run(args, parser)
    if args.command == "mvp":
        return _mvp_command.run(args, parser)
    if args.command == "management-listener":
        return _management_listener_command.run(args, parser)
    if args.command in {"mvp-demo", "mvp-acceptance"}:
        return _demo_command.run(args, parser)
    if args.command == "enforce":
        return _enforce_command.run(args, enforce_parser)
    if args.command == "assets":
        return _assets_command.run(args, assets_parser)
    if args.command == "revalidation":
        return _revalidation_command.run(args, revalidation_parser)
    if args.command == "auth":
        return _auth_command.run(args, auth_parser)
    if args.command == "metrics-check":
        return _metrics_check_command.run(args, parser)
    parser.print_help()
    return 0
