"""CLI registration and execution for mvp."""

import argparse
import sys


def _observation_feed(value: str) -> tuple[str, str]:
    kind, separator, path = value.partition("=")
    if separator != "=" or kind not in {"hubble", "tetragon", "management_listener"} or not path.strip():
        raise argparse.ArgumentTypeError(
            "observation feed must be hubble=PATH, tetragon=PATH or management_listener=PATH"
        )
    return kind, path


def add_parser(subparsers):
    commands = subparsers
    mvp_parser = commands.add_parser("mvp", help="로컬 Gateway와 선택한 backend 실행")
    _add_arguments_1(mvp_parser)
    _add_arguments_2(mvp_parser)
    mvp_parser.add_argument("--config", metavar="FILE.toml", help="MVPConfig TOML 설정(명시한 CLI 인자가 우선)")
    return mvp_parser


def _add_arguments_1(mvp_parser):
    mvp_parser.add_argument("--policy", metavar="PATH")
    mvp_parser.add_argument(
        "--policy-bundle",
        metavar="DIR",
        help="시작할 때 한 번 검증해 적용할 LAB 정책 묶음 디렉터리",
    )
    mvp_parser.add_argument("--gateway-port", type=int, default=8080, metavar="N")
    mvp_parser.add_argument("--engine-port", type=int, default=8088, metavar="N")
    mvp_parser.add_argument("--backend", choices=("mock", "vllm", "sglang"), default="mock")
    mvp_parser.add_argument("--backend-config", metavar="PATH")
    mvp_parser.add_argument("--data-dir", metavar="PATH")
    mvp_parser.add_argument("--relay-timeout", type=float, default=5.0, metavar="SECONDS")
    mvp_parser.add_argument("--stream-idle-timeout", type=float, default=30.0, metavar="SECONDS")
    mvp_parser.add_argument("--request-deadline", type=float, default=300.0, metavar="SECONDS")
    mvp_parser.add_argument("--max-concurrency", type=int, default=2, metavar="N")
    mvp_parser.add_argument("--max-connections", type=int, default=64, metavar="N")
    mvp_parser.add_argument("--request-header-timeout", type=float, default=5.0, metavar="SECONDS")
    mvp_parser.add_argument("--request-body-timeout", type=float, default=10.0, metavar="SECONDS")
    mvp_parser.add_argument("--environment", metavar="NAME")
    mvp_parser.add_argument("--run-id", metavar="ID")
    mvp_parser.add_argument("--revocations", metavar="PATH")
    mvp_parser.add_argument(
        "--policy-approvals",
        metavar="PATH",
        help="LAB 정책 승인 목록(runtime-policy-approvals/1.0)",
    )
    mvp_parser.add_argument(
        "--observation-exceptions",
        metavar="PATH",
        help="실행 예외(runtime-exec-exceptions/1.0); intent 1.1 필요",
    )
    mvp_parser.add_argument(
        "--observation-timeline",
        metavar="PATH",
        help="단계 시간표(runtime-phase-timeline/1.0); intent 1.1 필요",
    )
    mvp_parser.add_argument(
        "--target",
        metavar="PATH",
        help="대상 등록 파일(runtime-target/1.0). 없으면 대상 미등록",
    )
    target_pod_group = mvp_parser.add_mutually_exclusive_group()
    target_pod_group.add_argument(
        "--target-pod-status",
        action="store_true",
        help="대상 등록의 kubernetes selector로 Pod를 읽기 전용 조회",
    )
    target_pod_group.add_argument(
        "--target-pod-snapshot",
        metavar="PATH",
        help=("호스트가 쓴 runtime-target-pods-snapshot/1.0 파일로 대상 Pod를 관측"),
    )
    mvp_parser.add_argument(
        "--kube-context",
        metavar="NAME",
        help=(
            "Cilium 정책·대상 Pod 조회에 명시할 Kubernetes context. 생략하면 대상 등록의 "
            "kubernetes.context. Kubernetes 기능 사용 시 필수. 대상 등록과 다르면 시작하지 않음"
        ),
    )


def _add_arguments_2(mvp_parser):
    mvp_parser.add_argument(
        "--auth-config",
        metavar="PATH",
        help="시험용 인증 설정(runtime-auth/1.0). 없으면 인증 미적용",
    )
    mvp_parser.add_argument("--baseline", metavar="PATH")
    mvp_parser.add_argument("--manifest", metavar="PATH")
    mvp_parser.add_argument("--collector-interval", type=float, default=5.0, metavar="SECONDS")
    mvp_parser.add_argument("--asset-manifest", metavar="PATH")
    mvp_parser.add_argument("--asset-interval", type=float, default=60.0, metavar="SECONDS")
    mvp_parser.add_argument("--retention-max-age-days", type=float, default=7.0, metavar="DAYS")
    mvp_parser.add_argument("--retention-max-bytes", type=int, default=5 * 1024**3, metavar="BYTES")
    mvp_parser.add_argument("--retention-interval", type=float, default=3600.0, metavar="SECONDS")
    mvp_parser.add_argument(
        "--retention-enforce",
        action="store_true",
        help="보존 한도를 넘은 기록을 실제로 삭제(기본은 보고만)",
    )
    mvp_parser.add_argument("--retention-hold", metavar="PATH", help="삭제하지 않을 실행·요청 ID 목록")
    mvp_parser.add_argument("--lab-report", metavar="PATH")
    mvp_parser.add_argument("--cache-observation", metavar="PATH")
    mvp_parser.add_argument("--cache-observation-reader-gid", type=int)
    mvp_parser.add_argument("--cache-observation-producer-sha256")
    mvp_parser.add_argument("--cache-observation-binding-sha256")
    mvp_parser.add_argument("--native-receipt-feed", metavar="PATH")
    mvp_parser.add_argument("--native-receipt-reader-gid", type=int)
    mvp_parser.add_argument("--native-receipt-producer-sha256")
    mvp_parser.add_argument("--tetragon-state", metavar="PATH", help="호스트에서 수집한 Tetragon 정책·시험 상태 JSON")
    mvp_parser.add_argument(
        "--gpu-state", metavar="PATH", help="호스트 수집기가 쓴 GPU 사용률·메모리·온도 공개 상태 JSON"
    )
    mvp_parser.add_argument(
        "--engine-receipts",
        metavar="PATH",
        help="선택: 엔진 로그에서 요청 ID만 추출한 공개 JSON(tools.engine_receipts)",
    )
    mvp_parser.add_argument(
        "--tetragon-identities", metavar="PATH", help="노드에서 확인한 Tetragon 프로세스·컨테이너 식별 자료"
    )
    mvp_parser.add_argument(
        "--observation-feed",
        action="append",
        default=[],
        type=_observation_feed,
        metavar="KIND=PATH",
    )
    mvp_parser.add_argument(
        "--observation-heartbeat",
        action="append",
        default=[],
        type=_observation_feed,
        metavar="KIND=PATH",
    )
    mvp_parser.add_argument("--observation-origin", choices=("synthetic", "real"), default="real")
    mvp_parser.add_argument("--observation-environment", metavar="NAME")
    mvp_parser.add_argument("--observation-run-id", metavar="ID")
    mvp_parser.add_argument("--observation-intent", metavar="PATH")
    mvp_parser.add_argument("--cilium-policy-status", action="store_true")
    mvp_parser.add_argument("--collector-stale-after", type=float, default=15.0, metavar="SECONDS")


def _check_conflicts(args, parser):
    if args.policy_bundle is not None:
        conflicts = [
            option
            for option, value in (
                ("--policy", args.policy),
                ("--policy-approvals", args.policy_approvals),
                ("--target", args.target),
                ("--observation-exceptions", args.observation_exceptions),
                ("--observation-intent", args.observation_intent),
            )
            if value is not None
        ]
        if conflicts:
            parser.error("--policy-bundle은 다음 명시 옵션과 함께 쓸 수 없습니다: " + ", ".join(conflicts))


def _options_from_args(args):
    options = {
        "policy_path": args.policy or "examples/mvp/policy.json",
        "gateway_port": args.gateway_port,
        "engine_port": args.engine_port,
        "backend": args.backend,
        "backend_config_path": args.backend_config,
        "relay_timeout_s": args.relay_timeout,
        "stream_idle_timeout_s": args.stream_idle_timeout,
        "request_deadline_s": args.request_deadline,
        "max_concurrent_requests": args.max_concurrency,
        "max_connections": args.max_connections,
        "request_header_timeout_s": args.request_header_timeout,
        "request_body_timeout_s": args.request_body_timeout,
        "revocations_path": args.revocations,
        "policy_approvals_path": args.policy_approvals,
        "baseline_path": args.baseline,
        "manifest_path": args.manifest,
        "collector_interval_s": args.collector_interval,
        "collector_stale_after_s": args.collector_stale_after,
        "asset_manifest_path": args.asset_manifest,
        "asset_interval_s": args.asset_interval,
        "retention_max_age_days": args.retention_max_age_days,
        "retention_max_bytes": args.retention_max_bytes,
        "retention_interval_s": args.retention_interval,
        "retention_enforce": args.retention_enforce,
        "retention_hold_path": args.retention_hold,
        "environment": args.environment,
        "run_id": args.run_id,
        "lab_report_path": args.lab_report,
        "cache_observation_path": args.cache_observation,
        "cache_observation_reader_gid": args.cache_observation_reader_gid,
        "cache_observation_producer_sha256": args.cache_observation_producer_sha256,
        "cache_observation_binding_sha256": args.cache_observation_binding_sha256,
        "native_receipt_feed_path": args.native_receipt_feed,
        "native_receipt_reader_gid": args.native_receipt_reader_gid,
        "native_receipt_producer_sha256": args.native_receipt_producer_sha256,
        "tetragon_state_path": args.tetragon_state,
        "gpu_state_path": args.gpu_state,
        "engine_receipts_path": args.engine_receipts,
        "tetragon_identities_path": args.tetragon_identities,
        "observation_feeds": tuple(args.observation_feed),
        "observation_heartbeats": tuple(args.observation_heartbeat),
        "observation_origin": args.observation_origin,
        "observation_environment": args.observation_environment,
        "observation_run_id": args.observation_run_id,
        "observation_intent_path": args.observation_intent,
        "cilium_policy_status": args.cilium_policy_status,
        "auth_config_path": args.auth_config,
        "target_path": args.target,
        "observation_exceptions_path": args.observation_exceptions,
        "observation_timeline_path": args.observation_timeline,
        "target_pod_status": args.target_pod_status,
        "target_pod_snapshot": args.target_pod_snapshot,
        "kube_context": args.kube_context,
    }
    if hasattr(args, "cilium_timeout_s"):
        options["cilium_timeout_s"] = args.cilium_timeout_s
    return options


def run(args, parser) -> int:
    from serving_runtime.gateway.app import run
    from serving_runtime.config.config import MVPConfig
    from serving_runtime.policy.policy_bundle import verify_policy_bundle

    _check_conflicts(args, parser)
    try:
        options = _options_from_args(args)
        if args.policy_bundle is not None:
            bundle = verify_policy_bundle(args.policy_bundle)
        if args.data_dir is not None:
            options["data_dir"] = args.data_dir
        if args.policy_bundle is None:
            run(MVPConfig(**options))
        else:
            with bundle.materialized_files() as role_paths:
                bundle_options, bundle_status = bundle.mvp_options(tuple(args.observation_feed), role_paths=role_paths)
                options.update(bundle_options)
                options["policy_bundle"] = bundle_status
                run(MVPConfig(**options))
    except (OSError, ValueError) as error:
        print(f"MVP 실행 실패: {error}", file=sys.stderr)
        return 1
    return 0
