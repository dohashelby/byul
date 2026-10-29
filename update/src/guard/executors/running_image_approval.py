"""SUP-04의 제한된 실행 이미지 승인 관측. 패키지·entrypoint 전체 검증은 아니다.

기존 container_config가 collection_profile로 이 함수를 호출한다. 컨테이너를
만들거나 sentinel·파일 쓰기·패키지 import를 수행하지 않는다. 이미지 확인
구간의 전후 identity를 확인한 뒤 정상 추론을 별도로 수집한다.
"""
from __future__ import annotations

import re
from datetime import datetime

from guard.models import canonical_json, sha256_hex
from guard.runtime.kubernetes_binding import validate_container_binding, verify_owned_pod

from .http_common import functional_requests, functional_results


PROFILE = "running-image-approval-v1"
VERSION = "0.5.0"
POLICY_KEY = "approved_image_digests"
_PINNED = re.compile(r"[a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64}")
_IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}")
_CONTAINER_ID = re.compile(r"[0-9a-f]{64}")
_CONTAINER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
KUBERNETES_CONSUMER_VERSION = "0.1.1"


def _pinned(value):
    return type(value) is str and len(value) <= 512 and _PINNED.fullmatch(value) is not None


def _manifest_matches(pinned, declared):
    # BYUL environment declares a bare manifest digest. The repository comes
    # from the explicit target and is verified against Docker, never guessed.
    return declared == pinned or (type(declared) is str and _IMAGE_ID.fullmatch(declared)
                                  and declared == pinned.partition("@")[2])


def _timestamp(value):
    if type(value) is not str:
        return False
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None
    except ValueError:
        return False


def validate_image_approval_policy(values):
    """빈 목록은 명시적인 '승인한 이미지 없음'이다. 값을 보충·축소하지 않는다."""
    approved = values.get(POLICY_KEY)
    if type(approved) is not list or any(not _pinned(value) for value in approved):
        raise ValueError("approved_image_digests must be explicit repository@sha256 references")
    if len(approved) != len(set(approved)):
        raise ValueError("approved_image_digests must not contain duplicates")


def _target(ctx, configured_container):
    if (not ctx.env.hash_ok()
            or ctx.target.get("environment_ref") != ctx.env.ref().model_dump(mode="json")):
        return None
    engine = ctx.target.get("engine")
    if type(engine) is not dict:
        return None
    container = engine.get("container")
    pinned = engine.get("image_digest")
    run_id = ctx.target.get("run_id")
    if (type(container) is not str or not _CONTAINER.fullmatch(container)
            or configured_container is not None and configured_container != container
            or not _pinned(pinned) or not _manifest_matches(pinned, ctx.env.engine.image_digest)
            or type(run_id) is not str or not run_id):
        return None
    return container, pinned, run_id


def _snapshot(inspected, container, pinned, run_id):
    if type(inspected) is not dict:
        return None
    state, config = inspected.get("State"), inspected.get("Config")
    if type(state) is not dict or type(config) is not dict:
        return None
    labels = config.get("Labels")
    image_id = inspected.get("Image")
    if (type(labels) is not dict or labels.get("guard.run") != run_id
            or state.get("Running") is not True
            or not _timestamp(state.get("StartedAt"))
            or type(inspected.get("Id")) is not str or not _CONTAINER_ID.fullmatch(inspected["Id"])
            or inspected.get("Name") != "/" + container
            or type(image_id) is not str or not _IMAGE_ID.fullmatch(image_id)
            or config.get("Image") != pinned):
        return None
    # 원래 inspect·환경변수·명령 인자·호스트 mount는 저장하지 않는다.
    return {"container_id": inspected["Id"], "name": inspected["Name"],
            "image_id": image_id, "configured_image": pinned,
            "started_at": state["StartedAt"], "owned_run_id": run_id}


def analyze_kubernetes_running_image(env, target, before, after):
    """기존 A1 target과 {pod, namespace} 전후 쌍의 API 신원만 소비한다.

    수집·kubectl wrapper·새 target 계약은 만들지 않는다. API imageID가 pinned
    문자열과 같더라도 node runtime RepoDigest/source 계보 증명이 아니다.
    이 소비부에는 아직 그 공통 생산자가 없으므로 승인 관측을 만들지 않는다.
    """
    result = {
        "consumer_version": KUBERNETES_CONSUMER_VERSION,
        "interface_status": "PROPOSED_CONSUMER",
        "api_identity_stable": False,
        "identity_before": None, "identity_after": None,
        "runtime_image_relation_verified": False,
        "producer_authenticated": False,
        "diagnostic_codes": [],
    }
    try:
        if (not env.hash_ok() or type(target) is not dict
                or target.get("schema") != "guard-kubernetes-request-binding-target/1"
                or target.get("topology") != "kubernetes-owned-direct"
                or type(target.get("lab")) is not bool
                or target.get("environment_ref") != env.ref().model_dump(mode="json")):
            raise ValueError
        bound = target["kubernetes"]
        expected = bound["engine"]
        validate_container_binding(expected)
        if (type(bound["context"]) is not str
                or not re.fullmatch(r"[A-Za-z0-9._:@/-]{1,200}", bound["context"])
                or type(bound["run_id"]) is not str
                or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", bound["run_id"])
                or not _pinned(expected["image"])
                or target["engine"]["image_digest"] != expected["image"]
                or not _manifest_matches(expected["image"], env.engine.image_digest)):
            raise ValueError
    except (KeyError, TypeError, ValueError, AttributeError):
        result["diagnostic_codes"] = ["RUNNING_IMAGE_TARGET_UNVERIFIED"]
        return result

    owners = []
    for index, snapshot in enumerate((before, after)):
        try:
            pod, namespace = snapshot["pod"], snapshot["namespace"]
            if (pod.get("kind") != "Pod" or namespace.get("kind") != "Namespace"
                    or namespace["metadata"].get("name") != expected["namespace"]):
                raise ValueError
            owner = verify_owned_pod(pod, namespace["metadata"], expected, bound["run_id"])
        except (KeyError, TypeError, ValueError, AttributeError):
            result["diagnostic_codes"] = ["RUNNING_IMAGE_IDENTITY_UNVERIFIED" if index == 0
                                          else "RUNNING_IMAGE_SOURCE_CHANGED"]
            return result
        owners.append(owner)
        result["identity_before" if index == 0 else "identity_after"] = owner
    if owners[0] != owners[1]:
        result["diagnostic_codes"] = ["RUNNING_IMAGE_SOURCE_CHANGED"]
        return result
    result.update(api_identity_stable=True,
                  environment_ref=env.ref().model_dump(mode="json"),
                  owner_sha256=sha256_hex(canonical_json(owners[0])),
                  diagnostic_codes=["RUNNING_IMAGE_RUNTIME_RELATION_UNAVAILABLE"])
    return result


def collect_kubernetes_running_image_approval(ctx, before, after):
    """A1의 공통 수집/전송 연결용 전용 소비 경계.

    before/after는 동일 수집 구간에서 공통 생산자가 제공해야 한다. 지금은 API
    metadata만 있으므로 정상 추론도 시도하지 않는다. 승인 빈 목록을 포함해
    어느 정책에서도 API 선언만으로 실제 비승인 실행 FAIL을 만들지 않는다.
    """
    validate_image_approval_policy(ctx.policy_values)
    analysis = analyze_kubernetes_running_image(ctx.env, ctx.target, before, after)
    observations = {
        "running_image_identity_verified": {"value": False, "collected": True},
        "running_image_repo_digest": {"value": None, "collected": False},
        "running_image_approved": {"value": None, "collected": False},
        "running_image_unapproved": {"value": None, "collected": False},
        "supply_execution_scope_complete": {"value": False, "collected": True},
        "functional_probes_planned": {"value": [p.id for p in ctx.check.functional_probes], "collected": True},
        "functional_probe_results": {"value": {}, "collected": False},
    }
    normal = {"attempted": False, "succeeded": None,
              "detail": "Kubernetes runtime image relation unavailable; inference not attempted"}
    challenges = [{"step": step, "attempted": False,
                   "observations": {"profile": PROFILE, "scope": "Kubernetes API metadata only"}}
                  for step in ctx.check.challenge_steps]
    summary = {
        "profile": PROFILE, "kubernetes_image": analysis,
        "collection_errors": analysis["diagnostic_codes"],
        "not_exercised": ["runtime_repo_digest_relation", "image_source_lineage", "package_inventory",
                          "native_libraries", "entrypoint_mutation", "mounted_code_overrides",
                          "deployment_gate_challenge", "functional_inference"],
        "limitations": [
            "Kubernetes API identity is not node-runtime RepoDigest or image/source consumption proof.",
            "This API metadata consumer receives no node-runtime image/source relation.",
            "No approval, security PASS or confirmed unapproved execution follows from API imageID.",
            "Only the supplied before/after API snapshots are compared; collection times are not established.",
        ],
        "fixture": {"profile": PROFILE, "consumer_version": KUBERNETES_CONSUMER_VERSION,
                    "scope": "kubernetes-api-metadata-only"},
    }
    return observations, normal, challenges, summary


def collect_running_image_approval(docker, ctx, configured_container=None):
    """실행 이미지의 식별·승인 관측 후 기존 정상 대조를 재사용한다."""
    validate_image_approval_policy(ctx.policy_values)
    errors, before, after, digests = [], None, None, None
    target = _target(ctx, configured_container)
    inspect_attempted = False
    verified = False
    if target is None:
        errors.append("RUNNING_IMAGE_TARGET_UNVERIFIED")
    else:
        container, pinned, run_id = target
        try:
            inspect_attempted = True
            before = _snapshot(docker.inspect_json(container, timeout=10), container, pinned, run_id)
            if before is None:
                errors.append("RUNNING_IMAGE_IDENTITY_UNVERIFIED")
            else:
                # Config.Image 문자열을 image ID처럼 사용하지 않는다.
                # 실제 실행 중인 container.Image의 immutable ID로 조회한다.
                image = docker.image_info(before["image_id"], timeout=10)
                rows = image.get("digests") if type(image) is dict else None
                if (type(rows) is not list or not rows or len(rows) > 128
                        or any(not _pinned(value) for value in rows)
                        or pinned not in rows):
                    errors.append("RUNNING_IMAGE_REPODIGEST_UNVERIFIED")
                else:
                    digests = sorted(set(rows))
                after = _snapshot(docker.inspect_json(container, timeout=10), container, pinned, run_id)
                if after != before:
                    errors.append("RUNNING_IMAGE_SOURCE_CHANGED")
                verified = digests is not None and before == after
        except Exception:
            # 예외 원문·daemon stderr에는 비밀이 있을 수 있다.
            errors.append("RUNNING_IMAGE_COLLECTION_UNAVAILABLE")

    actual = target[1] if verified else None
    approved = actual in ctx.policy_values[POLICY_KEY] if verified else None
    # 이 사실은 기능 HTTP 수집보다 먼저 확정한다. 후속 정상 대조 오류가
    # 비승인 실행 이미지의 이미 확보한 식별 근거를 지우지 않는다.
    normal = {"attempted": False, "succeeded": None,
              "detail": "running-image identity unavailable; inference not attempted"}
    planned = [probe.id for probe in ctx.check.functional_probes]
    functional = dict.fromkeys(planned, "no_response")
    functional_attempted = False
    if verified:
        try:
            requests, probes = functional_requests(ctx, via="engine")
            planned = list(probes)
            functional_attempted = bool(requests)
            responses = ctx.cprobe(ctx.trusted_net, requests, ctx.timeout) if requests else []
            if (type(responses) is not list or len(responses) != len(requests)
                    or any(type(response) is not dict for response in responses)
                    or [r.get("id") for r in responses] != [r["id"] for r in requests]):
                raise ValueError("functional response correlation unavailable")
            results = dict(zip(probes, responses, strict=True))
            functional, _, normal = functional_results(probes, results, ctx.env.model.id)
            normal["detail"] = "실행 이미지 식별 후 신뢰 출발점의 엔진 정상 추론 표본"
            for (pid, probe), response in zip(probes.items(), responses, strict=True):
                ctx.record("trusted", "functional-engine", pid, probe.as_role, response)
        except Exception:
            errors.append("RUNNING_IMAGE_FUNCTIONAL_UNAVAILABLE")
            functional = dict.fromkeys(planned, "no_response")
            normal = {"attempted": functional_attempted, "succeeded": False,
                      "detail": "running-image normal control unavailable"}

    observations = {
        "running_image_identity_verified": {"value": verified, "collected": True},
        "running_image_repo_digest": {"value": actual, "collected": verified},
        "running_image_approved": {"value": approved, "collected": verified},
        "running_image_unapproved": {"value": not approved if verified else None, "collected": verified},
        "supply_execution_scope_complete": {"value": False, "collected": True},
        "functional_probes_planned": {"value": planned, "collected": True},
        "functional_probe_results": {"value": functional, "collected": functional_attempted},
    }
    # 원문 체크리스트의 package/entrypoint 변경 시험을 한 것처럼 표시하지 않는다.
    challenges = [{"step": step, "attempted": inspect_attempted if index == 0 else False,
                   "observations": {"profile": PROFILE,
                                    "scope": "running deployment image only" if index == 0 else "not exercised"}}
                  for index, step in enumerate(ctx.check.challenge_steps)]
    summary = {
        "profile": PROFILE, "identity_before": before, "identity_after": after,
        "resolved_repo_digests": digests, "image_identity_verified": verified,
        "collection_errors": errors,
        "not_exercised": ["package_inventory", "native_libraries", "entrypoint_mutation",
                          "mounted_code_overrides", "deployment_gate_challenge"],
        "limitations": [
            "Only the owned running deployment image and its pinned RepoDigest were identified.",
            "Repository manifests and local Docker image configuration IDs are different hash spaces.",
            "Approval values are operator declarations, not authenticated corporate approval.",
            "Approved image identity cannot establish full supply approval or security PASS.",
            "The original package/entrypoint mutation and deployment-gate trials were not performed.",
            "Identity stability covers the image metadata collection window; normal inference is collected afterward.",
        ],
        "fixture": {"profile": PROFILE,
                    "identity_fields": ["Id", "Name", "Image", "State.Running", "State.StartedAt",
                                        "Config.Image", "Config.Labels.guard.run"],
                    "digest_query": "image_info(container.Image)",
                    "normal": [probe.model_dump(mode="json") for probe in ctx.check.functional_probes]},
    }
    return observations, normal, challenges, summary
