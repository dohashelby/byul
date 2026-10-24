"""Development CLI for local inspection and the serving runtime MVP."""

import argparse
from collections.abc import Sequence
from importlib.metadata import version
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
    mvp_parser.add_argument("--baseline", metavar="PATH")
    mvp_parser.add_argument("--manifest", metavar="PATH")
    mvp_parser.add_argument("--collector-interval", type=float, default=5.0, metavar="SECONDS")
    mvp_parser.add_argument("--asset-manifest", metavar="PATH")
    mvp_parser.add_argument("--asset-interval", type=float, default=60.0, metavar="SECONDS")
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
        from .enforcement.tetragon import render_manifests as render_tetragon

        try:
            intent = load_intent(args.intent)
            manifests = render_cilium(intent)
            manifests.update(render_tetragon(intent))
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
        from .enforcement.intent import IntentError, load_intent

        try:
            binaries = None
            if args.intent is not None:
                binaries = load_intent(args.intent).allowed_engine_binaries
            result = ingest_file(
                args.input,
                args.data_dir,
                kind=args.kind,
                data_origin=args.origin,
                allowed_engine_binaries=binaries,
                environment=args.environment,
                run_id=args.run_id,
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
    parser.print_help()
    return 0
