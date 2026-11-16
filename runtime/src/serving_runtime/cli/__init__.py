"""Runtime command registration and dispatch."""

import argparse
import sys
from collections.abc import Sequence
from importlib.metadata import version

from . import agent as _agent
from . import file_info as _file_info
from . import bundle as _bundle
from . import mvp as _mvp
from . import management_listener as _management_listener
from . import demo as _demo
from . import assets as _assets
from . import revalidation as _revalidation
from . import auth as _auth
from . import metrics_check as _metrics_check
from . import enforce as _enforce

__all__ = ["main", "build_parser"]

_COMMANDS = {
    "agent": _agent,
    "file-info": _file_info,
    "bundle": _bundle,
    "mvp": _mvp,
    "management-listener": _management_listener,
    "mvp-demo": _demo,
    "mvp-acceptance": _demo,
    "assets": _assets,
    "revalidation": _revalidation,
    "auth": _auth,
    "metrics-check": _metrics_check,
    "enforce": _enforce,
}


def build_parser():
    parser = argparse.ArgumentParser(
        prog="serving-runtime",
        description="Serving Runtime 개발 도구: 로컬 파일 확인과 런타임 통제 MVP 실행·시험.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {version('serving-runtime')}")
    commands = parser.add_subparsers(dest="command")
    child_parsers = {}
    child_parsers["agent"] = _agent.add_parser(commands)
    child_parsers["file-info"] = _file_info.add_parser(commands)
    child_parsers["bundle"] = _bundle.add_parser(commands)
    child_parsers["mvp"] = _mvp.add_parser(commands)
    child_parsers["management-listener"] = _management_listener.add_parser(commands)
    _demo.add_parser(commands)
    child_parsers["assets"] = _assets.add_parser(commands)
    child_parsers["revalidation"] = _revalidation.add_parser(commands)
    child_parsers["auth"] = _auth.add_parser(commands)
    child_parsers["metrics-check"] = _metrics_check.add_parser(commands)
    child_parsers["enforce"] = _enforce.add_parser(commands)
    parser._command_parsers = child_parsers
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    config_file = None
    if arguments and arguments[0] == "mvp":
        from ..config.node_config import apply_node_defaults

        try:
            config_file = apply_node_defaults(parser._command_parsers["mvp"], arguments[1:])
        except OSError as error:
            print(f"MVP 실행 실패: {error}", file=sys.stderr)
            return 1
        except ValueError as error:
            parser.error(str(error))
    args = parser.parse_args(arguments)
    if args.command == "mvp" and args.config is not None and config_file is None:
        # The new option uses the exact spelling required by its preliminary parser.
        token = next(
            token for token in arguments[1:] if token.startswith("--") and "--config".startswith(token.split("=", 1)[0])
        )
        detail = token if "=" in token else f"{token} {args.config}"
        parser.error("unrecognized arguments: " + detail)
    command = _COMMANDS.get(args.command)
    if command is not None:
        context = (
            parser._command_parsers[args.command]
            if args.command in {"assets", "revalidation", "auth", "enforce"}
            else parser
        )
        return command.run(args, context)
    parser.print_help()
    return 0
