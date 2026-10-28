"""A1 Kubernetes binding을 소비하는 model hostPath 선언 분석. 수집 연결은 제안 상태."""
from guard.models import canonical_json, sha256_hex
from guard.runtime.kubernetes_binding import validate_capture_target, verify_owned_pod
from guard.collectors.model_mapping import validate_model_mapping


def _projection(pod, container, mapping):
    selected, = [c for c in pod["spec"]["containers"] if c["name"] == container]
    mounts = [m for m in selected.get("volumeMounts", []) if m.get("mountPath") == mapping["container_mount"]]
    mount, = mounts
    if ("subPath" in mount or "subPathExpr" in mount or type(mount.get("readOnly", False)) is not bool):
        raise ValueError
    volume, = [v for v in pod["spec"]["volumes"] if v["name"] == mount["name"]]
    host = volume["hostPath"]
    if (type(host) is not dict or host.get("path") != mapping["host_snapshot"]
            or host.get("type") != "Directory"):
        raise ValueError
    return {"declared_read_only": mount.get("readOnly", False), "host_snapshot_matches": True}


def analyze_model_mount(env, target, mapping, before, after):
    """before/after는 A1 수집부가 메모리로 제공할 {pod, namespace} 쌍이다.

    원문 Pod·env·annotations는 결과에 남기지 않는다. 설정과 실제 접근은 구분한다.
    결과는 기존 EVD로 연결되기 전의 제안 소비 자료이며 자동 security 판정이 아니다.
    """
    value = validate_model_mapping(env, mapping)
    result = {"collected": False, "observations": {}, "diagnostic_codes": [],
        "worker_access_verified": False, "producer_authenticated": False,
        "environment_ref": env.ref().model_dump(mode="json"), "interface_status": "PROPOSED_CONSUMER"}
    try:
        bound = validate_capture_target(target, env)
        owners, projected = [], []
        for snapshot in (before, after):
            owners.append(verify_owned_pod(snapshot["pod"], snapshot["namespace"]["metadata"],
                bound["engine"], bound["run_id"]))
            projected.append(_projection(snapshot["pod"], bound["engine"]["container"], value))
        if owners[0] != owners[1] or projected[0] != projected[1]:
            raise ValueError
    except (ValueError, TypeError, KeyError, AttributeError):
        result["diagnostic_codes"] = ["MODEL_MOUNT_SOURCE_UNVERIFIED"]
        return result
    result.update(collected=True, observations=projected[0], owner_sha256=sha256_hex(canonical_json(owners[0])))
    return result
