"""명시적 저장소↔served 이름 매핑 준비. 요청 생성 연결은 공통 경계가 담당한다."""
from copy import deepcopy
from pathlib import PurePosixPath
import re

SCHEMA = "guard.model-request-mapping/1"
FIELDS = {"schema", "environment_ref", "repository_id", "revision", "served_model_name",
          "host_snapshot", "container_mount", "inventory_sha256"}


def _absolute(value):
    return (type(value) is str and 1 < len(value) <= 2048 and value.startswith("/")
        and PurePosixPath(value).as_posix() == value and ".." not in PurePosixPath(value).parts
        and not any(ord(c) < 32 or ord(c) == 127 or c in "\\:" for c in value))


def validate_model_mapping(env, value):
    """ENV의 기존 모델 신원을 바꾸지 않고 기술적 API 별칭만 검증한다. I/O 없음."""
    try:
        if (not env.hash_ok() or type(value) is not dict or set(value) != FIELDS
                or value["schema"] != SCHEMA
                or value["environment_ref"] != env.ref().model_dump(mode="json")
                or type(value["repository_id"]) is not str
                or re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value["repository_id"]) is None
                or value["repository_id"] != env.model.id
                or type(value["revision"]) is not str or re.fullmatch(r"[0-9a-f]{40}", value["revision"]) is None
                or value["revision"] != env.model.revision
                or value["inventory_sha256"] != env.model.inventory_sha256
                or value["inventory_sha256"] is not None and (type(value["inventory_sha256"]) is not str
                    or re.fullmatch(r"[0-9a-f]{64}", value["inventory_sha256"]) is None)
                or type(value["served_model_name"]) is not str or not 1 <= len(value["served_model_name"]) <= 256
                or any(ord(c) < 32 or ord(c) == 127 for c in value["served_model_name"])
                or not _absolute(value["host_snapshot"]) or not _absolute(value["container_mount"])):
            raise ValueError
    except (KeyError, TypeError, AttributeError, ValueError):
        raise ValueError("MODEL_MAPPING_INVALID") from None
    return deepcopy(value)


def request_model_name(env, mapping):
    """공통 요청 생성부에 제공할 별칭. ENV/model/revision/Ref를 수정하지 않는다."""
    return validate_model_mapping(env, mapping)["served_model_name"]


def container_model_path(env, mapping, host_file):
    value = validate_model_mapping(env, mapping)
    if not _absolute(host_file):
        raise ValueError("MODEL_MAPPING_PATH_INVALID")
    try:
        relative = PurePosixPath(host_file).relative_to(value["host_snapshot"])
        if not relative.parts:
            raise ValueError
    except ValueError:
        raise ValueError("MODEL_MAPPING_PATH_INVALID") from None
    return str(PurePosixPath(value["container_mount"]) / relative)
