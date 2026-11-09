"""로컬 전용 컨테이너 실험실: 엔진(stub 또는 실제 vLLM) + nginx Gateway의 두 위상.

    exposed : 네트워크 1개(엔진·Gateway·외부 호출자). 엔진 포트를 127.0.0.1에도 공개 → 직통 가능.
    isolated: engine-net(--internal: 엔진·Gateway·신뢰 출발점), outside(Gateway·외부 호출자). 엔진은 공개하지 않음.

`up()`은 target.json(키 없음)과 env.yaml을 run_dir에 쓰고 target dict를 돌려준다. 정리는 Docker.cleanup()이 한다.
모든 포트는 127.0.0.1에만 바인드한다. 원격 Docker를 쓰지 않는다(DOCKER_HOST를 설정하지 않음).

엔진 launch와 증거 계약은 engine_provider가 소유한다. vllm 인자는 이전 호출자용 별칭이다.
"""

from __future__ import annotations

import json
import hashlib
import time
from pathlib import Path

import yaml

from guard.adapters.engine_provider import get_provider, ENGINE_PORT, CACHE_ENV
from guard.executors import http_probe
from guard.executors.content_common import redact_value
from guard.executors.http_probe import probe
from guard.runtime.docker import Docker, DockerError, Mount
from guard.runtime import generation_readiness

LAB = Path(__file__).resolve().parent / "assets"
NGINX_IMAGE = "nginx:1.27-alpine"
PYTHON_IMAGE = "python:3.12-slim"
GATEWAY_PORT = 8080
KNOWN_GATEWAY_ROUTES = [  # Gateway 설정에서 알려진 경로
    {"method": "GET", "path": "/v1/models"},
    {"method": "POST", "path": "/v1/completions"},
    {"method": "POST", "path": "/invocations"},
]
PROBE_SCRIPT = Path(http_probe.__file__).resolve()
T = 60  # docker 호출 기본 timeout(초)


def pull_images(docker: Docker) -> dict[str, str | None]:
    for img in (NGINX_IMAGE, PYTHON_IMAGE):
        if docker.image_digest(img, timeout=T) is None:
            docker.pull(img, timeout=600)
    return {img: docker.image_digest(img, timeout=T) for img in (NGINX_IMAGE, PYTHON_IMAGE)}


def _probe_in(docker: Docker, network: str, requests: list[dict], timeout: float, suffix: str) -> list[dict]:
    res = docker.run_once(
        PYTHON_IMAGE, suffix, network=network, timeout=timeout * len(requests) + 30,
        mounts=[Mount(PROBE_SCRIPT, "/probe/http_probe.py")], command=["python", "/probe/http_probe.py"],
        input=json.dumps({"timeout": timeout, "requests": requests}).encode(),
    )
    if res.returncode != 0:
        raise DockerError(f"probe container failed: {res.stderr.strip()[:300]}")
    return json.loads(res.stdout)


def _wait(fn, what: str, deadline_s: float = 30, interval: float = 0.5, alive=None) -> float:
    started = time.monotonic()
    end = started + deadline_s
    while time.monotonic() < end:
        if fn():
            return time.monotonic() - started
        if alive is not None and not alive():
            raise DockerError(f"lab: {what} exited before becoming ready")
        time.sleep(interval)
    raise DockerError(f"lab: {what} not ready after {deadline_s}s")


def _vllm_plan(vllm: dict):
    """Legacy public helper; launch knowledge belongs to the provider."""
    return get_provider('vllm').launch_plan(vllm)


# 컴파일 캐시 경로 환경변수 → exec 가능한 /guard-cache (실제 기동 오류 메시지로 확인한 것만, DECISIONS Step 3)


# 엔진 컨테이너 실행 권한 프로필(PLT-01). 모델 마운트는 모든 프로필에서 읽기 전용이다.
ENGINE_PROFILES = {
    "default": {},                                                    # Docker 기본(root, 기본 capability 14개)
    # 안전: capability 전부 제거·읽기 전용 루트·non-root. /tmp는 noexec, 컴파일 캐시만 별도 exec 가능 tmpfs(/guard-cache)
    "hardened": {"cap_drop": ["ALL"], "read_only": True,
                 "tmpfs": ["/tmp:rw,noexec,nosuid,size=2g,mode=1777", "/guard-cache:rw,exec,nosuid,size=2g,mode=1777"],
                 "user": "1000:1000", "security_opt": ["no-new-privileges:true"],
                 "env": {"HOME": "/tmp"}},
    # 위반(반대 방향): hardened와 같되 /tmp 자체에 exec 허용(정책 허용 목록에 없음)
    "tmp_exec": {"cap_drop": ["ALL"], "read_only": True, "tmpfs": ["/tmp:rw,exec,nosuid,size=2g,mode=1777"],
                 "user": "1000:1000", "security_opt": ["no-new-privileges:true"], "env": {"HOME": "/tmp"}},
    "overprivileged": {"cap_add": ["SYS_ADMIN"], "sentinel_mount": True},  # 위반: 추가 capability + 빈 sentinel 디렉토리 rw
}
SENTINEL_TARGET = "/guard-sentinel"


def _engine_profile(engine: str, vllm: dict | None, profile: str) -> dict:
    """실행과 비교 조건이 같은 고정 권한·캐시 환경을 사용한다."""
    if profile not in ENGINE_PROFILES:
        raise ValueError(profile)
    if engine == 'stub':
        return ENGINE_PROFILES[profile]
    return get_provider(engine).runtime_profile(vllm or {}, profile, ENGINE_PROFILES[profile])


def _public_environment(base: dict, prof: dict, credential_canary: bool) -> dict:
    return {**base, **prof.get("env", {}),
            **({"GUARD_TEST_CREDENTIAL": "EXAMPLE_NOT_A_SECRET"} if credential_canary else {})}


def _start_engine(docker: Docker, engine: str, networks: list[str], publish: list[int], vllm: dict | None,
                  profile: str = "default", sentinel_dir: Path | None = None, credential_canary: bool = False) -> str:
    prof = _engine_profile(engine, vllm, profile)
    if type(credential_canary) is not bool:
        raise ValueError("credential_canary must be a boolean")
    extra_mounts = []
    if prof.get("sentinel_mount"):
        if sentinel_dir is None or any(sentinel_dir.iterdir()):
            raise ValueError("overprivileged profile needs an empty sentinel_dir inside the project")
        extra_mounts.append(Mount(sentinel_dir.resolve(), SENTINEL_TARGET, read_only=False))
    opts = {k: prof[k] for k in ("cap_add", "cap_drop", "read_only", "tmpfs", "user", "security_opt") if k in prof}
    if engine == "stub":
        return docker.run_detached(PYTHON_IMAGE, "engine", networks=networks, publish=publish,
                                   mounts=[Mount(LAB / "stub_engine/server.py", "/lab/server.py"), *extra_mounts],
                                   public_env=_public_environment({}, prof, credential_canary), command=["python", "-u", "/lab/server.py"],
                                   timeout=T, **opts)
    plan = get_provider(engine).launch_plan(vllm)
    if plan.gpus and (not plan.gpu_devices or plan.limits is None):
        raise ValueError('ENGINE_GPU_ALLOCATION_CONTRACT_REQUIRED')
    assert all(m.read_only for m in plan.mounts)  # 모델 마운트는 항상 읽기 전용
    if plan.limits is not None:
        opts["limits"] = plan.limits
    if plan.entrypoint is not None:
        opts["entrypoint"] = plan.entrypoint
    if plan.gpu_devices is not None:
        opts['gpu_devices'] = plan.gpu_devices
    return docker.run_detached(plan.image, "engine", networks=networks, publish=publish,
                               mounts=[*plan.mounts, *extra_mounts], public_env=_public_environment(plan.env, prof, credential_canary),
                               command=plan.argv, gpus=plan.gpus, shm_size=plan.shm_size, timeout=T, **opts)


GATEWAY_PROFILES = {
    # 프로필 → 필요한 키 환경변수(값은 실행 시에만, 파일에 없음)
    "single": ("GUARD_ALLOWED_KEY",),                          # REQ-01: 허용 키 1개면 모든 경로
    "role_open": ("GUARD_GENERAL_KEY", "GUARD_ADMIN_KEY"),     # REQ-03 위반: 역할 무관 전부 통과
    "role_enforced": ("GUARD_GENERAL_KEY", "GUARD_ADMIN_KEY"), # REQ-03 안전: 일반 키는 추론 경로만
}


def capture_diagnostics(docker: Docker, run_dir: Path, *, secret_values: list[str], phase: str) -> dict:
    """Best-effort separate run diagnostic, captured before labelled resources are removed.

    This is not an EVD attachment or an automatic evidence-bundle member. Never
    copy raw daemon errors, Config.Env, requests, or unredacted log files.
    """
    path = run_dir / "managed-diagnostic.json"
    try:
        # A startup-failure capture must survive the application's final capture.
        if not path.exists():
            rows = {}
            for role in ("engine", "gw"):
                row = {}
                try:
                    row["state"] = docker.diagnostic_state(role, timeout=5)
                except Exception as exc:
                    row["state_error_type"] = type(exc).__name__
                else:
                    try:
                        result = docker.diagnostic_logs(role, timeout=5)
                        row["logs_returncode"] = result.returncode
                        if result.returncode == 0:
                            text = redact_value(result.stdout + result.stderr, secret_values)
                            encoded = text.encode("utf-8")
                            row["logs_truncated"] = len(encoded) > 32768
                            # Redact before clipping: even a credential crossing the saved boundary is removed.
                            row["logs"] = encoded[-32768:].decode("utf-8", "ignore")
                        else:
                            row["logs_error_type"] = "DockerCommandFailure"
                    except Exception as exc:
                        row["logs_error_type"] = type(exc).__name__
                rows[role] = row
            doc = {"schema": "guard-managed-diagnostic/1", "phase": phase if phase in {"startup-failure", "run-final"} else "unknown",
                   "containers": rows,
                   "scope": "Separate run diagnostic; not an EVD attachment or automatic evidence-bundle member.",
                   "redaction": "Known gateway credentials and labelled credential values are masked; arbitrary log content or personal data may remain.",
                   "limits": {"tail_lines_per_container": 80, "saved_log_bytes_per_container": 32768,
                              "timeout_seconds_per_command": 5,
                              "raw_single_line_bytes": "not hard-capped by the subprocess reader"}}
            run_dir.mkdir(parents=True, exist_ok=True)
            # The run directory is fresh and no existing capture is overwritten.
            payload = json.dumps(doc, ensure_ascii=False, indent=2).encode("utf-8")
            with path.open("xb") as out:
                out.write(payload)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        hash_path = path.with_suffix(".json.sha256")
        if not hash_path.exists():
            hash_path.write_text(digest + "  managed-diagnostic.json\n", encoding="ascii")
        return {"path": path.name, "sha256": digest}
    except Exception as exc:
        # Diagnostic collection must never replace the original execution failure or a completed violation.
        return {"error_type": type(exc).__name__}


def up(docker: Docker, topology: str, run_dir: Path, allowed_key: str | None = None, *, engine: str = "stub",
       vllm: dict | None = None, engine_options: dict | None = None, wait_ready: bool = True, gateway_profile: str = "single",
       gateway_keys: dict[str, str] | None = None, engine_profile: str = "default",
       sentinel_dir: Path | None = None, credential_canary: bool = False) -> dict:
    if vllm is not None and engine_options is not None:
        raise ValueError('ENGINE_OPTIONS_AMBIGUOUS')
    options = engine_options if engine_options is not None else vllm
    try:
        return _up(docker, topology, run_dir, allowed_key, engine=engine, vllm=options, wait_ready=wait_ready,
                   gateway_profile=gateway_profile, gateway_keys=gateway_keys, engine_profile=engine_profile,
                   sentinel_dir=sentinel_dir, credential_canary=credential_canary)
    except Exception:
        if run_dir.exists():
            capture_diagnostics(docker, run_dir, secret_values=[v for v in [allowed_key, *(gateway_keys or {}).values()] if v],
                                phase="startup-failure")
        raise


def _up(docker: Docker, topology: str, run_dir: Path, allowed_key: str | None = None, *, engine: str = "stub",
        vllm: dict | None = None, wait_ready: bool = True, gateway_profile: str = "single",
        gateway_keys: dict[str, str] | None = None, engine_profile: str = "default",
        sentinel_dir: Path | None = None, credential_canary: bool = False) -> dict:
    """Run the selected registered managed provider. vllm is a legacy options alias.

    gateway_profile: single(allowed_key) | role_open | role_enforced(gateway_keys: GUARD_GENERAL_KEY·GUARD_ADMIN_KEY).
    """
    if topology not in ("exposed", "isolated"):
        raise ValueError(topology)
    if type(credential_canary) is not bool:
        raise ValueError("credential_canary must be a boolean")
    if gateway_profile not in GATEWAY_PROFILES:
        raise ValueError(gateway_profile)
    secret_env = {"GUARD_ALLOWED_KEY": allowed_key} if gateway_profile == "single" else dict(gateway_keys or {})
    missing = [k for k in GATEWAY_PROFILES[gateway_profile] if not secret_env.get(k)]
    if missing:
        raise ValueError(f"gateway profile {gateway_profile} needs keys {missing}")
    provider = None if engine == 'stub' else get_provider(engine)
    if provider is not None and (not provider.managed_available or not vllm):
        raise ValueError('ENGINE_MANAGED_LAUNCH_UNSUPPORTED:' + engine)
    generation_profile = provider.generation_profile(vllm or {}) if provider else generation_readiness.profile(vllm or {})
    if generation_profile is not None and (provider is None or not wait_ready):
        raise ValueError("generation readiness requires a managed engine with wait_ready enabled")
    run_dir.mkdir(parents=True, exist_ok=True)
    images = pull_images(docker)
    ready_timeout = float(vllm.get("ready_timeout", 600)) if provider else 30.0

    publish = [ENGINE_PORT] if topology == "exposed" else []
    if topology == "exposed":
        net = docker.network_create("net", timeout=T)
        engine_name = _start_engine(docker, engine, [net], publish, vllm, engine_profile, sentinel_dir, credential_canary)
        engine_ip = docker.ip(engine_name, net, timeout=T)
        external_net = trusted_net = net
    else:
        engine_net = docker.network_create("engine", internal=True, timeout=T)
        outside = docker.network_create("outside", timeout=T)
        engine_name = _start_engine(docker, engine, [engine_net], publish, vllm, engine_profile, sentinel_dir, credential_canary)
        engine_ip = docker.ip(engine_name, engine_net, timeout=T)
        external_net, trusted_net = outside, engine_net

    gw_networks = [external_net] if topology == "exposed" else [external_net, trusted_net]
    gateway = docker.run_detached(
        NGINX_IMAGE, "gw", networks=gw_networks, publish=[GATEWAY_PORT],
        mounts=[Mount(LAB / "gateway" / gateway_profile, "/etc/nginx/templates")],
        public_env={"GUARD_ENGINE_UPSTREAM": f"{engine_ip}:{ENGINE_PORT}"},
        secret_env=secret_env, timeout=T,
    )
    gateway_ip = docker.ip(gateway, external_net, timeout=T)
    gw_host_port = docker.published_port(gateway, GATEWAY_PORT, timeout=T)
    _wait(lambda: probe({"method": "GET", "url": f"http://127.0.0.1:{gw_host_port}/health"}, 2)["outcome"] == 401, "gateway")

    engine_url = f"http://{engine_ip}:{ENGINE_PORT}"
    ready_seconds = None
    version = None
    if wait_ready:
        ready_seconds = _wait(
            lambda: _probe_in(docker, trusted_net, [{"id": "ready", "method": "GET", "url": f"{engine_url}/health"}],
                              2, "ready-engine")[0]["outcome"] == 200,
            f"{engine} engine", deadline_s=ready_timeout, interval=2 if provider else 0.5,
            alive=lambda: docker.is_running(engine_name, timeout=T),
        )
        if provider is not None:
            request = provider.version_request(engine_url)
            if request is not None:
                (v,) = _probe_in(docker, trusted_net, [request], 5, 'version')
                if v.get('outcome') == 200:
                    version = json.loads(v.get('body') or '{}').get('version')
            if generation_profile is not None:
                generation_readiness.run(run_dir, generation_profile, engine_url, trusted_net,
                    lambda network, requests, timeout: _probe_in(docker, network, requests, timeout, "generation-ready"),
                    secret_values=[value for value in secret_env.values() if value])

    direct_targets = [{"origin": "external", "url": engine_url}]
    if topology == "exposed":
        engine_host_port = docker.published_port(engine_name, ENGINE_PORT, timeout=T)
        direct_targets.append({"origin": "host", "url": f"http://127.0.0.1:{engine_host_port}"})

    if provider is not None:
        images[vllm['image']] = docker.image_digest(vllm['image'], timeout=T)
    launch_conditions = provider.launch_conditions(vllm, engine_profile, credential_canary) if provider else {}
    target = {
        # 실험실 stub 대상만 lab=true(→ EXAMPLE). 실제 vLLM 대상은 실제 실행 → MEASURED (DECISIONS 5a/5b)
        "lab": engine == "stub",
        "engine_kind": engine,
        "run_id": docker.run_id,
        "observed_version": version,
        "comparison_config": {"profile": "sandbox-v1", "topology": topology, "engine_profile": engine_profile,
                              "support_images": {k: v for k, v in images.items() if not vllm or k != vllm["image"]},
                              "gateway_profile": gateway_profile, "launch": launch_conditions},
        "topology": topology,
        "gateway_profile": gateway_profile,
        "engine_profile": engine_profile,
        "probe_image": PYTHON_IMAGE,
        "origins": {"external": {"network": external_net}, "trusted": {"network": trusted_net}},
        "gateway": {"url": f"http://{gateway_ip}:{GATEWAY_PORT}", "origin": "external",
                    "host_url": f"http://127.0.0.1:{gw_host_port}"},
        "engine": {"trusted_url": engine_url, "container": engine_name, "ready_timeout": ready_timeout,
                   "image_digest": images.get(vllm["image"] if vllm else PYTHON_IMAGE),
                   "ready_seconds": ready_seconds},
        "direct_targets": direct_targets,
        "gateway_known_routes": KNOWN_GATEWAY_ROUTES,
        "images": images,
    }
    if credential_canary:
        target["comparison_config"]["credential_canary"] = True
    if generation_profile is not None:
        target["comparison_config"]["generation_readiness"] = generation_profile
        target["generation_readiness"] = generation_readiness.reference(run_dir)
    (run_dir / "target.json").write_text(json.dumps(target, indent=2), encoding="utf-8")
    doc = env_document(topology, images) if engine == "stub" else engine_env_document(engine, topology, vllm, images, version)
    (run_dir / "env.yaml").write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return target


def env_document(topology: str, images: dict[str, str | None]) -> dict:
    """실험실 ENV(DECLARED). stub은 vLLM이 아니므로 engine.name은 lab-stub."""
    return {
        "data_origin": "DECLARED",
        "engine": {"name": "lab-stub", "version": "0.1.0", "source_commit": None, "image_digest": images.get(PYTHON_IMAGE)},
        "adapter": {"name": "vllm", "version": "0.1.0"},
        "runtime": {"nodes": 1, "tp_groups": 1, "gpu_count": 0, "gpu_model": None, "driver": None},
        "model": {"id": None, "revision": None, "inventory_sha256": None},
        "facts": {
            "gateway": {"value": True, "origin": "DECLARED", "source": f"lab/lab.py topology={topology}"},
        },
    }


def engine_env_document(engine: str, topology: str, vllm: dict, images: dict[str, str | None], version: str | None) -> dict:
    """실제 vLLM 위상의 ENV(DECLARED: 사람이 정한 설정 + 확인 결과를 옮겨 적은 것)."""
    return {
        "data_origin": "DECLARED",
        "engine": {
            "name": engine,
            "version": version or vllm.get("expected_version"),
            "source_commit": vllm.get("source_commit_observed"),
            "image_digest": images.get(vllm["image"]),
            "source_commit_status": vllm.get("source_commit_status", "UNVERIFIED"),
            "expected_source_commit": vllm.get("expected_source_commit"),
        },
        "adapter": {"name": engine, "version": get_provider(engine).adapter().version},
        "runtime": {"nodes": 1, "tp_groups": 1, "gpu_count": len(vllm['gpu_devices']) if vllm.get('gpu_devices') is not None else (1 if vllm.get("gpu") else 0), "gpu_model": vllm.get("gpu_model"),
                    "driver": vllm.get("driver"), "platform": vllm.get("platform"),
                    **({"cpu": vllm["cpu"]} if vllm.get("cpu") else {})},
        "model": {
            "id": vllm["model"], "revision": vllm["revision"],
            "inventory_sha256": vllm.get("inventory_sha256"),  # 비교용: 마운트한 snapshot 전체
            **({"files_read_by_engine": {"file_count": vllm["files_read_by_engine_count"],
                                         "inventory_sha256": vllm["files_read_by_engine_sha256"]}}
               if vllm.get("files_read_by_engine_sha256") else {}),  # 참고용
        },
        "facts": {
            "gateway": {"value": True, "origin": "DECLARED", "source": f"lab/lab.py topology={topology} engine={engine}"},
        },
    }


def vllm_env_document(topology, vllm, images, version):
    """Compatibility entry point for saved launch tools."""
    return engine_env_document('vllm', topology, vllm, images, version)
