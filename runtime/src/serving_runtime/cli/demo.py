"""CLI registration and execution for demo."""


def add_parser(subparsers):
    commands = subparsers
    demo_parser = commands.add_parser("mvp-demo", help="실행 중인 MVP 시연 사례 전송")
    demo_parser.add_argument("--gateway", default="http://127.0.0.1:8080", metavar="URL")
    demo_parser.add_argument("--data-dir", metavar="PATH")
    acceptance_parser = commands.add_parser(
        "mvp-acceptance",
        help="격리된 MVP·S1·S2·S3A·가짜 SGLang·S4 수용 시험 실행",
    )
    acceptance_parser.add_argument("--report-dir", metavar="PATH")
    return (demo_parser, acceptance_parser)


def run(args, parser) -> int:
    if args.command == "mvp-demo":
        from serving_runtime.devtools.demo import run_cli

        return run_cli(args.gateway, args.data_dir)
    if args.command == "mvp-acceptance":
        from serving_runtime.devtools.acceptance import run_cli

        return run_cli(args.report_dir)
