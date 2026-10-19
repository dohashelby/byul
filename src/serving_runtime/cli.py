"""Basic command entry point; no runtime controls are implemented yet."""

import argparse
from collections.abc import Sequence
from importlib.metadata import version


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="serving-runtime",
        description="Serving Runtime 개발용 실행 진입점. 현재는 도움말과 버전 조회만 제공합니다.",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {version('serving-runtime')}"
    )
    parser.parse_args(argv)
    parser.print_help()
    return 0
