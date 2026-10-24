"""Development CLI for local inspection and the serving runtime MVP."""

import argparse
from collections.abc import Sequence
from importlib.metadata import version
import hashlib
import json
import os
from pathlib import Path
import sys

from .file_info import inspect_file


def _observation_feed(value: str) -> tuple[str, str]:
    kind, separator, path = value.partition("=")
    if separator != "=" or kind not in {"hubble", "tetragon"} or not path.strip():
        raise argparse.ArgumentTypeError(
            "observation feed must be hubble=PATH or tetragon=PATH"
        )
    return kind, path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="serving-runtime",
        description="Serving Runtime 개발 도구: 로컬 파일 확인과 런타임 통제 MVP 실행·시험.",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {version('serving-runtime')}"
    )
    commands = parser.add_subparsers(dest="command")
    file_parser = commands.add_parser("file-info", help="파일 크기와 SHA-256 확인")
    file_parser.add_argument("path", type=Path, help="확인할 일반 파일 경로")
    mvp_parser = commands.add_parser("mvp", help="로컬 Gateway와 선택한 backend 실행")
    mvp_parser.add_argument("--policy", default="examples/mvp/policy.json", metavar="PATH")
    mvp_parser.add_argument("--gateway-port", type=int, default=8080, metavar="N")
    mvp_parser.add_argument("--engine-port", type=int, default=8088, metavar="N")
    mvp_parser.add_argument("--backend", choices=("mock", "vllm", "sglang"), default="mock")
    mvp_parser.add_argument("--backend-config", metavar="PATH")
    mvp_parser.add_argument("--data-dir", metavar="PATH")
    mvp_parser.add_argument("--relay-timeout", type=float, default=5.0, metavar="SECONDS")
    mvp_parser.add_argument(
        "--stream-idle-timeout", type=float, default=30.0, metavar="SECONDS"
    )
    mvp_parser.add_argument(
        "--request-deadline", type=float, default=300.0, metavar="SECONDS"
    )
    mvp_parser.add_argument("--max-concurrency", type=int, default=2, metavar="N")
    mvp_parser.add_argument("--max-connections", type=int, default=64, metavar="N")
    mvp_parser.add_argument(
        "--request-header-timeout", type=float, default=5.0, metavar="SECONDS"
    )
    mvp_parser.add_argument(
        "--request-body-timeout", type=float, default=10.0, metavar="SECONDS"
    )
    mvp_parser.add_argument("--environment", metavar="NAME")
    mvp_parser.add_argument("--run-id", metavar="ID")
    mvp_parser.add_argument("--revocations", metavar="PATH")
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
    mvp_parser.add_argument(
        "--target-pod-status",
        action="store_true",
        help="대상 등록의 kubernetes selector로 Pod를 읽기 전용 조회(kind-runtime-lab 전용)",
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
    mvp_parser.add_argument(
        "--observation-origin", choices=("synthetic", "real"), default="real"
    )
    mvp_parser.add_argument("--observation-environment", metavar="NAME")
    mvp_parser.add_argument("--observation-run-id", metavar="ID")
    mvp_parser.add_argument("--observation-intent", metavar="PATH")
    mvp_parser.add_argument("--cilium-policy-status", action="store_true")
    mvp_parser.add_argument(
        "--collector-stale-after", type=float, default=15.0, metavar="SECONDS"
    )
    demo_parser = commands.add_parser("mvp-demo", help="실행 중인 MVP 시연 사례 전송")
    demo_parser.add_argument("--gateway", default="http://127.0.0.1:8080", metavar="URL")
    demo_parser.add_argument("--data-dir", metavar="PATH")
    acceptance_parser = commands.add_parser(
        "mvp-acceptance",
        help="격리된 MVP-01~11·S1-01~06·S2-01~06·S3A-01~08 수용 시험 실행",
    )
    acceptance_parser.add_argument("--report-dir", metavar="PATH")
    assets_parser = commands.add_parser("assets", help="모델 파일 승인 목록 도구")
    assets_commands = assets_parser.add_subparsers(dest="assets_command")
    snapshot_parser = assets_commands.add_parser(
        "snapshot", help="현재 파일로 목록 초안 생성(관측값, 승인은 사람이)"
    )
    snapshot_parser.add_argument("--root", required=True, metavar="DIR")
    snapshot_parser.add_argument("--out", required=True, metavar="PATH")
    revalidation_parser = commands.add_parser(
        "revalidation", help="재검증 요청 조회 연동 도구"
    )
    revalidation_commands = revalidation_parser.add_subparsers(
        dest="revalidation_command"
    )
    revalidation_export_parser = revalidation_commands.add_parser(
        "export", help="열린 재검증 요청을 새 JSON 파일로 내보내기"
    )
    revalidation_export_parser.add_argument("--data-dir", required=True, metavar="DIR")
    revalidation_export_parser.add_argument("--out", required=True, metavar="FILE")
    auth_parser = commands.add_parser("auth", help="시험용 인증 설정 도구")
    auth_commands = auth_parser.add_subparsers(dest="auth_command")
    auth_commands.add_parser(
        "hash-token",
        help="표준 입력의 토큰을 인증 설정용 SHA-256으로 변환(토큰은 출력하지 않음)",
    )
    metrics_parser = commands.add_parser(
        "metrics-check", help="저장한 /metrics 응답과 backend 지표 대응표 대조"
    )
    metrics_parser.add_argument("--backend-config", required=True, metavar="PATH")
    metrics_parser.add_argument("--input", required=True, metavar="FILE")
    metrics_parser.add_argument("--json", action="store_true", help="JSON으로 출력")
    enforce_parser = commands.add_parser(
        "enforce", help="Cilium·Tetragon 후보 생성과 관측 이벤트 변환"
    )
    enforce_commands = enforce_parser.add_subparsers(dest="enforce_command")
    render_parser = enforce_commands.add_parser(
        "render", help="통제 의도에서 클러스터 미검증 JSON 후보 생성"
    )
    render_parser.add_argument("--intent", required=True, metavar="PATH")
    render_parser.add_argument("--out", required=True, metavar="DIR")
    ingest_parser = enforce_commands.add_parser(
        "ingest", help="Hubble·Tetragon JSON Lines를 관측 기록으로 변환"
    )
    ingest_parser.add_argument("--kind", required=True, choices=("hubble", "tetragon"))
    ingest_parser.add_argument("--input", required=True, metavar="FILE")
    ingest_parser.add_argument("--origin", required=True, choices=("synthetic", "real"))
    ingest_parser.add_argument("--data-dir", required=True, metavar="DIR")
    ingest_parser.add_argument("--intent", metavar="PATH")
    ingest_parser.add_argument("--environment", metavar="NAME")
    ingest_parser.add_argument("--run-id", metavar="ID")
    ingest_parser.add_argument(
        "--exceptions", metavar="PATH", help="승인 실행 예외(runtime-exec-exceptions/1.0)"
    )
    ingest_parser.add_argument(
        "--timeline", metavar="PATH", help="단계 시간표(runtime-phase-timeline/1.0)"
    )
    cache_check_parser = enforce_commands.add_parser(
        "cache-check", help="캐시 세대 디렉터리의 소유자·권한·다른 세대를 점검(읽기 전용)"
    )
    cache_check_parser.add_argument("--intent", required=True, metavar="PATH")
    cache_check_parser.add_argument(
        "--root", metavar="DIR", help="cache_root 대신 점검할 디렉터리(사본 점검용)"
    )
    args = parser.parse_args(argv)
    if args.command == "file-info":
        try:
            info = inspect_file(args.path)
        except (OSError, ValueError) as error:
            print(f"파일 확인 실패: {error}", file=sys.stderr)
            return 1
        print(f"파일: {str(args.path)!r}")
        print(f"크기(bytes): {info.size_bytes}")
        print(f"SHA-256: {info.sha256}")
        return 0
    if args.command == "mvp":
        from .mvp.app import run
        from .mvp.config import MVPConfig

        try:
            options = {
                "policy_path": args.policy,
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
            }
            if args.data_dir is not None:
                options["data_dir"] = args.data_dir
            run(MVPConfig(**options))
        except (OSError, ValueError) as error:
            print(f"MVP 실행 실패: {error}", file=sys.stderr)
            return 1
        return 0
    if args.command == "mvp-demo":
        from .mvp.demo import run_cli

        return run_cli(args.gateway, args.data_dir)
    if args.command == "mvp-acceptance":
        from .mvp.acceptance import run_cli

        return run_cli(args.report_dir)
    if args.command == "enforce" and args.enforce_command == "render":
        from .enforcement.cilium import render_manifests as render_cilium
        from .enforcement.cilium import write_manifests
        from .enforcement.intent import IntentError, load_intent
        from .enforcement.security import render_pod_security
        from .enforcement.tetragon import render_manifests as render_tetragon

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
        print(
            f"{len(written)}개 생성: Cilium 1.20.3 / Tetragon 1.7.1 문법 기준 후보, "
            "클러스터 미적용·미검증"
        )
        return 0
    if args.command == "enforce" and args.enforce_command == "ingest":
        from .enforcement.events import ingest_file
        from .enforcement.execpolicy import ExecPolicy
        from .enforcement.intent import IntentError, load_intent

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
        print(
            f"처리 {result['processed']}건 · 저장 {result['accepted']}건 · "
            f"거부 {len(result['rejected'])}건"
        )
        for rejected in result["rejected"]:
            print(
                f"거부 line {rejected['line']}: {rejected['reason']}",
                file=sys.stderr,
            )
        return 0
    if args.command == "enforce" and args.enforce_command == "cache-check":
        from .enforcement.cache import check_cache
        from .enforcement.intent import IntentError, load_intent

        try:
            report = check_cache(load_intent(args.intent), root_override=args.root)
        except (OSError, ValueError, IntentError) as error:
            print(f"캐시 점검 실패: {error}", file=sys.stderr)
            return 1
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["state"] in {"ok", "other_generations_present"} else 3
    if args.command == "enforce":
        enforce_parser.print_help()
        return 0
    if args.command == "assets" and args.assets_command == "snapshot":
        from .mvp.assets import snapshot

        out = Path(args.out)
        try:
            document = snapshot(args.root)
            encoded = (
                json.dumps(document, ensure_ascii=False, indent=2) + "\n"
            ).encode("utf-8")
            descriptor = os.open(
                out,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
            )
            try:
                with os.fdopen(descriptor, "wb") as output:
                    descriptor = -1
                    output.write(encoded)
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
        except (OSError, ValueError) as error:
            print(f"목록 생성 실패: {error}", file=sys.stderr)
            return 1
        print(f"{len(document['files'])}개 파일 목록을 {out}에 썼습니다.")
        print("이 목록은 현재 파일의 관측값입니다. 승인 목록으로 쓰기 전에 사람이 검토해야 합니다.")
        return 0
    if args.command == "assets":
        assets_parser.print_help()
        return 0
    if args.command == "revalidation" and args.revalidation_command == "export":
        from .mvp.revalidation import export_requests

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
    if args.command == "auth" and args.auth_command == "hash-token":
        from getpass import getpass

        from .mvp.auth import token_digest

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
    if args.command == "metrics-check":
        from .mvp.backend import (
            load_backend_config,
            parse_prometheus_metrics,
            unmapped_metric_families,
        )

        try:
            backend = load_backend_config(args.backend_config, environ={})
            payload = Path(args.input).read_bytes()
            fields = parse_prometheus_metrics(payload, backend.metrics_map)
        except (OSError, ValueError) as error:
            print(f"지표 대응표 대조 실패: {error}", file=sys.stderr)
            return 1
        unmapped = unmapped_metric_families(payload, backend.metrics_map)
        report = {
            "schema": "runtime-metric-map-check/1.0",
            "backend_kind": backend.kind,
            "input": str(args.input),
            "input_sha256": hashlib.sha256(payload).hexdigest(),
            "fields": {
                name: {
                    key: item[key]
                    for key in (
                        "metric",
                        "type",
                        "unit",
                        "state",
                        "declared_type",
                        "type_check",
                        "help",
                        "label_keys",
                    )
                }
                | {"sample_count": len(item["samples"])}
                for name, item in fields.items()
            },
            "unmapped_families": unmapped,
            "note": "verified stays false in the backend config; this report is review evidence only",
        }
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(f"backend: {backend.kind} · 입력 SHA-256 {report['input_sha256'][:12]}…")
            for name, item in report["fields"].items():
                print(
                    f"- {name}: {item['metric']} · {item['state']} · 선언 형식 "
                    f"{item['declared_type'] or '없음'}({item['type_check']}) · 표본 {item['sample_count']}"
                )
            print(f"대응표에 없는 지표 {len(unmapped)}개")
        ok = all(item["state"] == "ok" for item in report["fields"].values())
        return 0 if ok else 3
    parser.print_help()
    return 0
