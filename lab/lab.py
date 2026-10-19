"""로컬 전용 컨테이너 실험실: 엔진(stub 또는 실제 vLLM) + nginx Gateway의 두 위상.

    exposed : 네트워크 1개(엔진·Gateway·외부 호출자). 엔진 포트를 127.0.0.1에도 공개 → 직통 가능.
    isolated: engine-net(--internal: 엔진·Gateway·신뢰 출발점), outside(Gateway·외부 호출자). 엔진은 공개하지 않음.

`up()`은 target.json(키 없음)과 env.yaml을 run_dir에 쓰고 target dict를 돌려준다. 정리는 Docker.cleanup()이 한다.
모든 포트는 127.0.0.1에만 바인드한다. 원격 Docker를 쓰지 않는다(DOCKER_HOST를 설정하지 않음).

vLLM 엔진 인자는 `adapters/vllm.py`의 launch_plan()만 쓴다(여기서 엔진 인자를 만들지 않는다).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import yaml

from guard.adapters.base import LaunchSpec
from guard.adapters.vllm import CONTAINER_PORT, VllmAdapter
from guard.executors import http_probe
from guard.executors.http_probe import probe
from guard.runtime.docker import Docker, DockerError, Mount

LAB = Path(__file__).resolve().parent
NGINX_IMAGE = "nginx:1.27-alpine"
PYTHON_IMAGE = "python:3.11-slim"
ENGINE_PORT = CONTAINER_PORT
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
    spec = LaunchSpec(
        model=vllm["model"], port=ENGINE_PORT, image=vllm["image"],
        options={"revision": vllm["revision"], "models_dir": Path(vllm["models_dir"]).expanduser(),
                 "device": "gpu" if vllm.get("gpu") else "cpu",
                 **({"cpu_kv_cache_gib": vllm["cpu_kv_cache_gib"]} if "cpu_kv_cache_gib" in vllm else {})},
    )
    return VllmAdapter().launch_plan(spec)


def _start_engine(docker: Docker, engine: str, networks: list[str], publish: list[int], vllm: dict | None) -> str:
    if engine == "stub":
        return docker.run_detached(PYTHON_IMAGE, "engine", networks=networks, publish=publish,
                                   mounts=[Mount(LAB / "stub_engine/server.py", "/lab/server.py")],
                                   command=["python", "-u", "/lab/server.py"], timeout=T)
    plan = _vllm_plan(vllm)
    return docker.run_detached(plan.image, "engine", networks=networks, publish=publish, mounts=plan.mounts,
                               public_env=plan.env, command=plan.argv, gpus=plan.gpus, shm_size=plan.shm_size,
                               timeout=T)


def up(docker: Docker, topology: str, run_dir: Path, allowed_key: str, *, engine: str = "stub",
       vllm: dict | None = None, wait_ready: bool = True) -> dict:
    """engine: "stub" | "vllm". vllm: guard.toml [vllm] 섹션(image, model, revision, models_dir, gpu, ready_timeout ...)."""
    if topology not in ("exposed", "isolated"):
        raise ValueError(topology)
    if engine not in ("stub", "vllm") or (engine == "vllm" and not vllm):
        raise ValueError(f"engine {engine!r} (vllm needs the [vllm] config)")
    run_dir.mkdir(parents=True, exist_ok=True)
    images = pull_images(docker)
    ready_timeout = float(vllm.get("ready_timeout", 600)) if engine == "vllm" else 30.0

    publish = [ENGINE_PORT] if topology == "exposed" else []
    if topology == "exposed":
        net = docker.network_create("net", timeout=T)
        engine_name = _start_engine(docker, engine, [net], publish, vllm)
        engine_ip = docker.ip(engine_name, net, timeout=T)
        external_net = trusted_net = net
    else:
        engine_net = docker.network_create("engine", internal=True, timeout=T)
        outside = docker.network_create("outside", timeout=T)
        engine_name = _start_engine(docker, engine, [engine_net], publish, vllm)
        engine_ip = docker.ip(engine_name, engine_net, timeout=T)
        external_net, trusted_net = outside, engine_net

    gw_networks = [external_net] if topology == "exposed" else [external_net, trusted_net]
    gateway = docker.run_detached(
        NGINX_IMAGE, "gw", networks=gw_networks, publish=[GATEWAY_PORT],
        mounts=[Mount(LAB / "gateway", "/etc/nginx/templates")],
        public_env={"GUARD_ENGINE_UPSTREAM": f"{engine_ip}:{ENGINE_PORT}"},
        secret_env={"GUARD_ALLOWED_KEY": allowed_key}, timeout=T,
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
            f"{engine} engine", deadline_s=ready_timeout, interval=2 if engine == "vllm" else 0.5,
            alive=lambda: docker.is_running(engine_name, timeout=T),
        )
        if engine == "vllm":
            (v,) = _probe_in(docker, trusted_net, [{"id": "version", "method": "GET", "url": f"{engine_url}/version",
                                                    "want_body": True}], 5, "version")
            if v.get("outcome") == 200:
                version = json.loads(v.get("body") or "{}").get("version")

    direct_targets = [{"origin": "external", "url": engine_url}]
    if topology == "exposed":
        engine_host_port = docker.published_port(engine_name, ENGINE_PORT, timeout=T)
        direct_targets.append({"origin": "host", "url": f"http://127.0.0.1:{engine_host_port}"})

    if engine == "vllm":
        images[vllm["image"]] = docker.image_digest(vllm["image"], timeout=T)
    target = {
        # 실험실 stub 대상만 lab=true(→ EXAMPLE). 실제 vLLM 대상은 실제 실행 → MEASURED (DECISIONS 5a/5b)
        "lab": engine == "stub",
        "engine_kind": engine,
        "run_id": docker.run_id,
        "topology": topology,
        "probe_image": PYTHON_IMAGE,
        "origins": {"external": {"network": external_net}, "trusted": {"network": trusted_net}},
        "gateway": {"url": f"http://{gateway_ip}:{GATEWAY_PORT}", "origin": "external",
                    "host_url": f"http://127.0.0.1:{gw_host_port}"},
        "engine": {"trusted_url": engine_url, "container": engine_name, "ready_timeout": ready_timeout,
                   "ready_seconds": ready_seconds},
        "direct_targets": direct_targets,
        "gateway_known_routes": KNOWN_GATEWAY_ROUTES,
        "images": images,
    }
    (run_dir / "target.json").write_text(json.dumps(target, indent=2), encoding="utf-8")
    doc = env_document(topology, images) if engine == "stub" else vllm_env_document(topology, vllm, images, version)
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


def vllm_env_document(topology: str, vllm: dict, images: dict[str, str | None], version: str | None) -> dict:
    """실제 vLLM 위상의 ENV(DECLARED: 사람이 정한 설정 + 확인 결과를 옮겨 적은 것)."""
    return {
        "data_origin": "DECLARED",
        "engine": {
            "name": "vllm",
            "version": version or vllm.get("expected_version"),
            "source_commit": vllm.get("source_commit_observed"),
            "image_digest": images.get(vllm["image"]),
            "source_commit_status": vllm.get("source_commit_status", "UNVERIFIED"),
            "expected_source_commit": vllm.get("expected_source_commit"),
        },
        "adapter": {"name": "vllm", "version": VllmAdapter().version},
        "runtime": {"nodes": 1, "tp_groups": 1, "gpu_count": 1 if vllm.get("gpu") else 0, "gpu_model": vllm.get("gpu_model"),
                    "driver": vllm.get("driver"), "platform": vllm.get("platform")},
        "model": {
            "id": vllm["model"], "revision": vllm["revision"],
            "inventory_sha256": vllm.get("inventory_sha256"),  # 비교용: 마운트한 snapshot 전체
            **({"files_read_by_engine": {"file_count": vllm["files_read_by_engine_count"],
                                         "inventory_sha256": vllm["files_read_by_engine_sha256"]}}
               if vllm.get("files_read_by_engine_sha256") else {}),  # 참고용
        },
        "facts": {
            "gateway": {"value": True, "origin": "DECLARED", "source": f"lab/lab.py topology={topology} engine=vllm"},
        },
    }
