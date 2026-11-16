"""CLI registration and execution for agent."""


def add_parser(subparsers):
    commands = subparsers
    from ..operations.agent import add_arguments

    agent_parser = commands.add_parser("agent", help="플랫폼 운영 요청 조회·검증·실행")
    add_arguments(agent_parser)
    return agent_parser


def run(args, parser) -> int:
    from ..operations.agent import main as agent_main

    return agent_main(args)
