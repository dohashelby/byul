"""CLI registration and execution for bundle."""

import json
import sys


def add_parser(subparsers):
    commands = subparsers
    bundle_parser = commands.add_parser("bundle", help="LAB 정책 묶음 확인")
    bundle_commands = bundle_parser.add_subparsers(dest="bundle_command")
    bundle_verify_parser = bundle_commands.add_parser("verify", help="정책 묶음의 구조·파일·유효성을 확인")
    bundle_verify_parser.add_argument("--bundle-dir", required=True, metavar="DIR")
    bundle_build_parser = bundle_commands.add_parser("build", help="CONFIRMED 플랫폼 정책에서 묶음 생성")
    bundle_build_parser.add_argument("--policy", required=True, metavar="FILE")
    bundle_build_parser.add_argument("--policy-sha256", required=True, metavar="SHA256")
    bundle_build_parser.add_argument("--server-values", required=True, metavar="FILE")
    bundle_build_parser.add_argument("--intent", required=True, metavar="FILE")
    bundle_build_parser.add_argument("--output-parent", required=True, metavar="DIR")
    bundle_build_parser.add_argument("--bundle-id", required=True)
    bundle_apply_parser = bundle_commands.add_parser("apply", help="서버 묶음 배치·재시작·상태 확인")
    bundle_apply_source = bundle_apply_parser.add_mutually_exclusive_group(required=True)
    bundle_apply_source.add_argument("--bundle-dir", metavar="DIR")
    bundle_apply_source.add_argument("--existing", metavar="ID", help="루트에 이미 배치된 묶음으로 전환")
    bundle_apply_parser.add_argument("--bundle-root", required=True, metavar="DIR")
    bundle_apply_parser.add_argument("--context", required=True)
    bundle_apply_parser.add_argument("--namespace", required=True)
    bundle_apply_parser.add_argument("--kubeconfig", metavar="FILE", help="모든 kubectl 호출에 명시할 kubeconfig")
    bundle_apply_parser.add_argument(
        "--status-url", required=True, help="Gateway 내부 포트를 담은 로컬 URL; 새 임시 port-forward를 엽니다"
    )
    bundle_apply_parser.add_argument(
        "--token-env-file", metavar="FILE", help="RUNTIME_VIEWER_TOKEN을 읽을 env 파일(조회가 비보호면 생략 가능)"
    )
    bundle_apply_parser.add_argument("--dry-run", action="store_true")
    bundle_apply_parser.add_argument("--operation-result", action="store_true", help="에이전트용 제한된 JSON 결과")
    bundle_apply_parser.add_argument(
        "--deployment-guard", metavar="FILE", help="현재 외부 승인·필수 UPDATE 근거를 적용/복귀 전에 검증"
    )
    return bundle_parser


def _run_verify(args):
    from serving_runtime.policy.policy_bundle import verify_policy_bundle

    try:
        bundle = verify_policy_bundle(args.bundle_dir)
    except (OSError, ValueError) as error:
        print(f"정책 묶음 검증 실패: {error}", file=sys.stderr)
        return 1
    print(f"정책 묶음 검증 성공: {bundle.bundle_id}")
    print(f"bundle.json SHA-256: {bundle.sha256}")
    print(f"파일 수: {len(bundle.files)}")
    return 0


def _run_build(args):
    from serving_runtime.operations.bundle_tools import build_bundle

    try:
        bundle = build_bundle(
            args.policy, args.policy_sha256, args.server_values, args.intent, args.output_parent, args.bundle_id
        )
    except (OSError, ValueError) as error:
        print(f"정책 묶음 생성 실패: {error}", file=sys.stderr)
        return 1
    print(f"정책 묶음 생성·검증 성공: {bundle.bundle_id}; 파일 {len(bundle.files)}개")
    print(f"policy_ref.sha256: {bundle.policy_ref['sha256']}; bundle.json SHA-256: {bundle.sha256}")
    print("승인 목록은 플랫폼 승인 기록의 자기 신고 사본이며 승인 검증이 아닙니다.")
    return 0


def _run_apply(args):
    from serving_runtime.operations.bundle_tools import apply_bundle

    try:
        result = apply_bundle(
            args.bundle_dir,
            args.bundle_root,
            args.context,
            args.namespace,
            args.status_url,
            args.token_env_file,
            kubeconfig=args.kubeconfig,
            dry_run=args.dry_run,
            existing=args.existing,
            deployment_guard_path=args.deployment_guard,
        )
    except (OSError, ValueError, RuntimeError) as error:
        if args.operation_result:
            restored = getattr(error, "outcome", {}).get("state") == "rolled_back"
            print(
                json.dumps(
                    {
                        "state": "rolled_back" if restored else "unknown",
                        "code": "ROLLED_BACK" if restored else "STATUS_UNKNOWN",
                    }
                )
            )
        else:
            print(f"정책 묶음 적용 실패: {error}", file=sys.stderr)
            if getattr(error, "outcome", None) is not None:
                print(json.dumps(error.outcome, ensure_ascii=False), file=sys.stderr)
        return 1
    if args.operation_result:
        print(json.dumps({"state": "succeeded", "code": "OK"}))
        return 0
    if result.get("deployment_guard") is not None:
        print("현재 외부 승인·필수 UPDATE 근거 확인 완료")
        print(
            json.dumps(
                {
                    "deployment_guard": result["deployment_guard"],
                    "final_verification": result.get("final_verification"),
                },
                ensure_ascii=False,
            )
        )
    print(
        f"묶음 {'적용 계획' if result['dry_run'] else '적용·상태 확인'} 성공: "
        f"{result['previous']} -> {result['bundle_id']}"
    )
    return 0


def run(args, parser) -> int:
    if args.bundle_command == "verify":
        return _run_verify(args)
    if args.bundle_command == "build":
        return _run_build(args)
    if args.bundle_command == "apply":
        return _run_apply(args)
    parser.print_help()
    return 0
