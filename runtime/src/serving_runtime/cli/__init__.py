"""Development CLI for local inspection and the serving runtime MVP."""

import argparse
from collections.abc import Sequence
from importlib.metadata import version
import json
import os
from pathlib import Path
import signal
import sys
import threading


def _observation_feed(value: str) -> tuple[str, str]:
    kind, separator, path = value.partition("=")
    if separator != "=" or kind not in {"hubble", "tetragon", "management_listener"} or not path.strip():
        raise argparse.ArgumentTypeError(
            "observation feed must be hubble=PATH, tetragon=PATH or management_listener=PATH"
        )
    return kind, path


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
    mvp_parser = commands.add_parser("mvp", help="로컬 Gateway와 선택한 backend 실행")
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
            "kubernetes.context, 그것도 없으면 kind-runtime-lab. 대상 등록과 다르면 시작하지 않음"
        ),
    )
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
    management_parser = commands.add_parser(
        "management-listener",
        help="엔진의 허용된 읽기 전용 관리 경로만 중계",
    )
    management_parser.add_argument("--listen-host", default="127.0.0.1", metavar="HOST")
    management_parser.add_argument("--listen-port", type=int, default=8081, metavar="N")
    management_parser.add_argument("--engine-url", required=True, metavar="URL")
    management_parser.add_argument(
        "--allow-path",
        action="append",
        metavar="PATH",
        help="정확히 허용할 경로(반복 가능; 생략 시 /metrics,/health,/v1/models,/version)",
    )
    management_parser.add_argument("--timeout", type=float, default=5.0, metavar="SECONDS")
    management_parser.add_argument("--max-response-bytes", type=int, default=1024 * 1024, metavar="BYTES")
    management_parser.add_argument(
        "--credential-file",
        metavar="PATH",
        help="엔진 Bearer token 파일(backend credential.file과 같은 읽기 규칙)",
    )
    management_parser.add_argument(
        "--record-file",
        metavar="PATH",
        help="최소 접근 JSONL 추가 기록(접근 기록은 항상 stdout에도 출력)",
    )
    management_parser.add_argument("--heartbeat-file", metavar="PATH", help="연속 관리접근 수집기의 writer 상태 파일")
    for field in ("target-ref", "run-id", "environment", "policy-ref"):
        management_parser.add_argument("--" + field, help="관리 접근 감사에 남길 설정 기반 참조")
    demo_parser = commands.add_parser("mvp-demo", help="실행 중인 MVP 시연 사례 전송")
    demo_parser.add_argument("--gateway", default="http://127.0.0.1:8080", metavar="URL")
    demo_parser.add_argument("--data-dir", metavar="PATH")
    acceptance_parser = commands.add_parser(
        "mvp-acceptance",
        help="격리된 MVP·S1·S2·S3A·가짜 SGLang·S4 수용 시험 실행",
    )
    acceptance_parser.add_argument("--report-dir", metavar="PATH")
    from . import assets as _assets_command

    assets_parser = _assets_command.add_parser(commands)
    from . import revalidation as _revalidation_command

    revalidation_parser = _revalidation_command.add_parser(commands)
    from . import auth as _auth_command

    auth_parser = _auth_command.add_parser(commands)
    from . import metrics_check as _metrics_check_command

    metrics_parser = _metrics_check_command.add_parser(commands)
    enforce_parser = commands.add_parser("enforce", help="Cilium·Tetragon 후보 생성과 관측 이벤트 변환")
    enforce_commands = enforce_parser.add_subparsers(dest="enforce_command")
    sync_parser = enforce_commands.add_parser("sync", help="현재 검증 묶음의 외부 정책 동기화(기본 dry-run)")
    source = sync_parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--bundle-dir")
    source.add_argument("--existing")
    sync_parser.add_argument("--bundle-root", default="/srv/serving/bundles")
    for flag in ("context", "namespace"):
        sync_parser.add_argument("--" + flag, required=True)
    for flag in ("kubeconfig", "token-env-file", "status-url"):
        sync_parser.add_argument("--" + flag)
    sync_parser.add_argument("--components", nargs="+", choices=("tetragon", "cilium"), default=["tetragon"])
    sync_parser.add_argument("--cilium-apply", action="store_true")
    sync_parser.add_argument("--execute", action="store_true")
    sync_parser.add_argument("--lock-file", default="/tmp/serving-runtime-enforcement.lock")
    render_parser = enforce_commands.add_parser("render", help="통제 의도에서 클러스터 미검증 JSON 후보 생성")
    render_parser.add_argument("--intent", required=True, metavar="PATH")
    render_parser.add_argument("--out", required=True, metavar="DIR")
    ingest_parser = enforce_commands.add_parser("ingest", help="Hubble·Tetragon JSON Lines를 관측 기록으로 변환")
    ingest_parser.add_argument("--kind", required=True, choices=("hubble", "tetragon"))
    ingest_parser.add_argument("--input", required=True, metavar="FILE")
    ingest_parser.add_argument("--origin", required=True, choices=("synthetic", "real"))
    ingest_parser.add_argument("--data-dir", required=True, metavar="DIR")
    ingest_parser.add_argument("--intent", metavar="PATH")
    ingest_parser.add_argument("--environment", metavar="NAME")
    ingest_parser.add_argument("--run-id", metavar="ID")
    ingest_parser.add_argument("--exceptions", metavar="PATH", help="승인 실행 예외(runtime-exec-exceptions/1.0)")
    ingest_parser.add_argument("--timeline", metavar="PATH", help="단계 시간표(runtime-phase-timeline/1.0)")
    cache_check_parser = enforce_commands.add_parser(
        "cache-check", help="캐시 세대 디렉터리의 소유자·권한·다른 세대를 점검(읽기 전용)"
    )
    cache_check_parser.add_argument("--intent", required=True, metavar="PATH")
    cache_check_parser.add_argument("--root", metavar="DIR", help="cache_root 대신 점검할 디렉터리(사본 점검용)")
    phase_signals_parser = enforce_commands.add_parser(
        "phase-signals",
        help="엔진 로그의 확인된 표지를 단계 시간표로 변환",
    )
    phase_signals_parser.add_argument("--engine", required=True, choices=("vllm", "sglang"))
    phase_signals_parser.add_argument("--log", required=True, metavar="PATH")
    phase_signals_parser.add_argument("--year", required=True, type=int, metavar="YYYY")
    phase_signals_parser.add_argument("--tz", required=True, metavar="OFFSET", help="명시적 UTC offset(예: +00:00)")
    phase_signals_parser.add_argument("--out", required=True, metavar="PATH")
    phase_signals_parser.add_argument(
        "--follow", action="store_true", help="파일 교체·잘림을 따라가거나 --log - 표준입력을 계속 읽기"
    )
    phase_signals_parser.add_argument(
        "--health-url", metavar="URL", help="follow 중 init/compile 뒤 200을 추론 시작 근거로 사용"
    )
    phase_signals_parser.add_argument("--health-interval", type=float, default=2.0, metavar="SECONDS")
    existing_group = phase_signals_parser.add_mutually_exclusive_group()
    existing_group.add_argument("--overwrite", action="store_true", help="기존 timeline을 버리고 새 follow 상태로 교체")
    existing_group.add_argument("--resume", action="store_true", help="기존 timeline 상태와 입력 위치에서 이어가기")
    args = parser.parse_args(argv)
    if args.command == "agent":
        return _agent_command.run(args, parser)
    if args.command == "file-info":
        return _file_info_command.run(args, parser)
    if args.command == "bundle" and args.bundle_command == "verify":
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
    if args.command == "bundle" and args.bundle_command == "build":
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
    if args.command == "bundle" and args.bundle_command == "apply":
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
    if args.command == "mvp":
        from serving_runtime.gateway.app import run
        from serving_runtime.config.config import MVPConfig
        from serving_runtime.policy.policy_bundle import verify_policy_bundle

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

        try:
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
            if args.policy_bundle is not None:
                bundle = verify_policy_bundle(args.policy_bundle)
            if args.data_dir is not None:
                options["data_dir"] = args.data_dir
            if args.policy_bundle is None:
                run(MVPConfig(**options))
            else:
                with bundle.materialized_files() as role_paths:
                    bundle_options, bundle_status = bundle.mvp_options(
                        tuple(args.observation_feed), role_paths=role_paths
                    )
                    options.update(bundle_options)
                    options["policy_bundle"] = bundle_status
                    run(MVPConfig(**options))
        except (OSError, ValueError) as error:
            print(f"MVP 실행 실패: {error}", file=sys.stderr)
            return 1
        return 0
    if args.command == "management-listener":
        from serving_runtime.gateway.management_listener import ManagementListenerConfig, run

        try:
            options = {
                "listen_host": args.listen_host,
                "listen_port": args.listen_port,
                "engine_url": args.engine_url,
                "timeout_s": args.timeout,
                "max_response_bytes": args.max_response_bytes,
                "credential_file": args.credential_file,
                "record_path": args.record_file,
                "heartbeat_path": args.heartbeat_file,
                "target_ref": args.target_ref,
                "run_id": args.run_id,
                "environment": args.environment,
                "policy_ref": args.policy_ref,
            }
            if args.allow_path:
                options["allowed_paths"] = tuple(args.allow_path)
            run(ManagementListenerConfig.build(**options))
        except (OSError, ValueError) as error:
            print(f"관리 listener 실행 실패: {error}", file=sys.stderr)
            return 1
        return 0
    if args.command == "mvp-demo":
        from serving_runtime.devtools.demo import run_cli

        return run_cli(args.gateway, args.data_dir)
    if args.command == "mvp-acceptance":
        from serving_runtime.devtools.acceptance import run_cli

        return run_cli(args.report_dir)
    if args.command == "enforce" and args.enforce_command == "sync":
        from ..enforcement.sync_cli import main as sync_main

        return sync_main(args)
    if args.command == "enforce" and args.enforce_command == "render":
        from ..enforcement.cilium import render_manifests as render_cilium
        from ..enforcement.cilium import write_manifests
        from ..enforcement.intent import IntentError, load_intent
        from ..enforcement.security import render_pod_security
        from ..enforcement.tetragon import render_manifests as render_tetragon

        try:
            intent = load_intent(args.intent)
            manifests = render_cilium(intent)
            manifests.update(render_tetragon(intent))
            pod_security = render_pod_security(intent)
            if pod_security is not None:
                manifests["pod-security-candidate.json"] = pod_security
            written = write_manifests(manifests, args.out)
        except (OSError, ValueError, IntentError) as error:
            print(f"통제 후보 생성 실패: {error}", file=sys.stderr)
            return 1
        blocked_rules = len(
            {rule for rule, mode in intent.tetragon_rule_modes if mode == "enforce"}
            | {rule for rule, modes in intent.tetragon_allowed_modes if "enforce" in modes}
        )
        blocked_summary = f", 차단 규칙 {blocked_rules}개(미검증)" if blocked_rules else ""
        print(
            f"{len(written)}개 생성: Cilium 1.20.3 / Tetragon 1.7.1 문법 기준 후보, "
            f"클러스터 미적용·미검증{blocked_summary}"
        )
        return 0
    if args.command == "enforce" and args.enforce_command == "ingest":
        from ..enforcement.events import ingest_file
        from ..enforcement.execpolicy import ExecPolicy
        from ..enforcement.intent import IntentError, load_intent

        try:
            binaries = None
            exec_policy = None
            if args.intent is not None:
                intent = load_intent(args.intent)
                binaries = intent.allowed_engine_binaries
                if intent.schema != "runtime-enforcement-intent/1.0":
                    exec_policy = ExecPolicy(
                        intent,
                        exceptions_path=args.exceptions,
                        timeline_path=args.timeline,
                    )
            elif args.exceptions or args.timeline:
                raise ValueError("--exceptions and --timeline need --intent (schema 1.1)")
            result = ingest_file(
                args.input,
                args.data_dir,
                kind=args.kind,
                data_origin=args.origin,
                allowed_engine_binaries=binaries,
                environment=args.environment,
                run_id=args.run_id,
                exec_policy=exec_policy,
            )
        except (OSError, UnicodeError, ValueError, IntentError) as error:
            print(f"관측 변환 실패: {error}", file=sys.stderr)
            return 1
        print(f"처리 {result['processed']}건 · 저장 {result['accepted']}건 · 거부 {len(result['rejected'])}건")
        if args.kind == "hubble":
            print(
                "Hubble 손실 레코드 "
                f"{result['lost_events_records']}건 · 손실 이벤트 "
                f"{result['lost_events_total']}건 · source별 "
                f"{json.dumps(result['lost_events_by_source'], ensure_ascii=False, sort_keys=True)} · "
                f"최근 손실 {result['last_lost_at'] or '–'}"
            )
        for rejected in result["rejected"]:
            print(
                f"거부 line {rejected['line']}: {rejected['reason']}",
                file=sys.stderr,
            )
        return 0
    if args.command == "enforce" and args.enforce_command == "cache-check":
        from ..enforcement.cache import check_cache
        from ..enforcement.intent import IntentError, load_intent

        try:
            report = check_cache(load_intent(args.intent), root_override=args.root)
        except (OSError, ValueError, IntentError) as error:
            print(f"캐시 점검 실패: {error}", file=sys.stderr)
            return 1
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["state"] in {"ok", "other_generations_present"} else 3
    if args.command == "enforce" and args.enforce_command == "phase-signals":
        from ..enforcement.phase_signals import extract_phase_timeline, follow_phase_timeline

        output = Path(args.out)
        if not args.follow and (args.log == "-" or args.health_url is not None or args.overwrite or args.resume):
            print(
                "단계 신호 변환 실패: --log -, --health-url, --overwrite, --resume는 --follow와 함께 사용해야 합니다",
                file=sys.stderr,
            )
            return 1
        if args.follow:
            stop = threading.Event()
            previous_handler = None
            if threading.current_thread() is threading.main_thread():
                previous_handler = signal.getsignal(signal.SIGTERM)
                signal.signal(signal.SIGTERM, lambda _signum, _frame: stop.set())
            try:
                timeline = follow_phase_timeline(
                    args.log,
                    output,
                    engine=args.engine,
                    year=args.year,
                    timezone_offset=args.tz,
                    existing="overwrite" if args.overwrite else "resume" if args.resume else "fail",
                    health_url=args.health_url,
                    health_interval=args.health_interval,
                    stop_event=stop,
                )
            except (OSError, UnicodeError, ValueError) as error:
                print(f"단계 신호 follow 실패: {error}", file=sys.stderr)
                return 1
            finally:
                if previous_handler is not None:
                    signal.signal(signal.SIGTERM, previous_handler)
            extraction = timeline["extraction"]
            print(
                f"follow 종료 · 상태 {extraction['follow']['state']} · 단계 {len(timeline['entries'])}개 · "
                f"처리 {extraction['lines_read']}줄"
            )
            return 0
        descriptor = -1
        try:
            timeline = extract_phase_timeline(
                args.log,
                engine=args.engine,
                year=args.year,
                timezone_offset=args.tz,
            )
            encoded = (json.dumps(timeline, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            descriptor = os.open(
                output,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
            )
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(encoded)
        except (OSError, UnicodeError, ValueError) as error:
            if descriptor >= 0:
                os.close(descriptor)
            print(f"단계 신호 변환 실패: {error}", file=sys.stderr)
            return 1
        extraction = timeline["extraction"]
        print(
            f"단계 {len(timeline['entries'])}개 · 처리 {extraction['lines_read']}줄 · "
            f"해석 안 함 {extraction['unparsed_lines']}줄 · 생략 경계 "
            f"{len(extraction['omitted_boundaries'])}개"
        )
        print(
            f"마지막 위치: line {extraction['last_position']['line']}, byte {extraction['last_position']['byte_offset']}"
        )
        return 0
    if args.command == "enforce":
        enforce_parser.print_help()
        return 0
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
