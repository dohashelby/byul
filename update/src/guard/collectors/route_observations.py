"""REQ-02의 소유 파일 수집. 생산자 인가·소비·제품 판정 연결은 미검증이다."""
from __future__ import annotations

from pathlib import Path
import re

from guard.collectors.file import load_environment
from guard.executors.native_observations import validate_capture
from guard.executors.owned_capture import read_owned_json, strict_json, validate_owned_source
from guard.models import DataOrigin


def validate_inputs(env, target, source):
    """정확한 ENV Ref와 소유 수집 인수를 확인하되 Docker는 조회하지 않는다."""
    if (not env.hash_ok() or type(target) is not dict
            or type(target.get("run_id")) is not str
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", target["run_id"])):
        raise ValueError("NATIVE_ROUTE_CAPTURE_INPUT_INVALID")
    validate_owned_source(**source)


def load_inputs(env_path, target_path, source):
    env = load_environment(Path(env_path))
    with Path(target_path).open("rb") as stream:
        target = strict_json(stream.read(65537))
    validate_inputs(env, target, source)
    return env, target


def validate_output(output, repository):
    """기존 부모 아래 저장소 밖의 새 파일만 허용하며 링크는 따라 쓰지 않는다."""
    output = Path(output).absolute()
    if (output.exists() or output.is_symlink() or not output.parent.is_dir()
            or output.resolve().is_relative_to(Path(repository).resolve())
            or any(part.is_symlink() or part.is_junction() or (part / ".git").exists()
                   for part in output.parents)):
        raise ValueError("NATIVE_ROUTE_CAPTURE_OUTPUT_INVALID")


def capture(docker, target, env, *, container, image, path):
    source = {"container": container, "image": image, "path": path}
    validate_inputs(env, target, source)
    packet, provenance = read_owned_json(docker, target, **source)
    summary = validate_capture(packet, kind="route", env_ref=env.ref().model_dump(mode="json"),
                               run_id=target["run_id"], image=image)
    # File transport cannot upgrade an example or authenticate a measured producer.
    origin = DataOrigin.EXAMPLE if env.data_origin == DataOrigin.EXAMPLE else DataOrigin.DECLARED
    return {"schema": "guard-route-native-attachment/1", "capture": provenance, "record": packet,
            "summary": summary, "product_connected": False, "data_origin": origin.value,
            "origin_detail": "owned file bytes captured; producer assertions not authenticated",
            "environment_ref": env.ref().model_dump(mode="json"), "security": "UNKNOWN"}
