"""REQ13 폐쇄 공개 전송과 기존 두 프로필의 분리. 실제 K8/엔진 실증이 아니다."""
from copy import deepcopy
import hashlib
import json
import sys

import pytest

from guard.models import canonical_json
from guard.runtime import kubernetes_http as kube
from guard.runtime.bounded import capture_pipes
from tests.test_kubernetes_sampling import World as PodWorld, PRIVATE
from tests.test_kubernetes_request_binding import env, target
from tests.test_stream_probe import _server

TOKEN = "ab" * 16


def request(transport, slot, token=TOKEN):
    primary = slot == "primary"
    return {"id": "req13-error-" + slot, "mode": "json", "method": "POST",
        "url": transport.endpoint.geturl() + ("/v1/chat/completions" if primary else "/tokenize"),
        "headers": {"X-Request-Id": "byul-req13-error-" + token + "-" + slot},
        "body": ({"model": transport.api_model, "messages": [{"role": "user", "content": "hello"}],
                  "max_tokens": "b2error-" + token[:12], "stream": False} if primary else
                 {"model": transport.api_model, "prompt": "hello", "add_special_tokens": False, "return_token_strs": False})}


class World(PodWorld):
    def __init__(self, value=None, damage=None):
        super().__init__(value or target())
        self.damage = damage
    def runner(self, argv, **kwargs):
        if "exec" not in argv:
            return super().runner(argv, **kwargs)
        spec = json.loads(kwargs["input_bytes"]); req, = spec["requests"]
        self.execs.append(req); self.after_exec = True
        assert "--container=probe" in argv and "-i" in argv and spec["timeout"] <= 120
        primary = req["id"] == "req13-error-primary"
        body = {"error": {"message": PRIVATE + " " + req["body"]["max_tokens"]}} if primary else {
            "tokens": [1, 2], "count": 2, "max_model_len": 128}
        raw = json.dumps(body)
        row = {"id": req["id"], "mode": "json", "method": "POST", "outcome": 400 if primary else 200,
            "body": raw, "body_bytes": len(raw.encode()), "body_sha256": hashlib.sha256(raw.encode()).hexdigest(),
            "body_truncated": False, "utf8_valid": True, "capture_complete": True, "eof": True}
        pipe = {"complete": True, "bounded_at_source": True, "timed_out": False, "truncated": False}
        if self.damage == "wrong-id": row["id"] = "before"
        elif self.damage == "partial": row["capture_complete"] = False
        elif self.damage == "oversized": row["body"] = PRIVATE * 10000
        elif self.damage == "cleanup": pipe.update(pipes_closed=False, read_errors=["child_reap"])
        elif self.damage == "timeout": raise TimeoutError(PRIVATE)
        return {"returncode": 0, "stdout_bytes": canonical_json([row]), "stderr_bytes": PRIVATE.encode(), "capture": pipe}


def transport(world=None, **kwargs):
    world = world or World()
    return kube.KubernetesHTTP(world.target, env(), duration=60, runner=world.runner,
                               profile=kube.VALIDATION_ERROR_PROFILE, **kwargs), world


def send(value, slot, **kwargs):
    return value("kubernetes-probe", [request(value, slot, **kwargs)], timeout=10)


def test_three_public_requests_exact_alias_and_body_free_report():
    value, world = transport()
    assert value.profile == "kubernetes-owned-validation-error-http-v1"
    assert value.api_model != env().model.id and value.environment_ref == env().ref().model_dump(mode="json")
    results = [send(value, slot)[0] for slot in ("before", "primary", "after")]
    assert [x["outcome"] for x in results] == [200, 400, 200]
    assert PRIVATE in results[1]["body"] and len(results[1]["body_sha256"]) == 64
    report = value.report()
    assert report["binding_sha256"] == hashlib.sha256(canonical_json(value.binding)).hexdigest()
    assert all(x["binding_verified_before_after"] for x in report["requests"])
    assert not report["collection_gap"] and not report["cleanup_errors"]
    assert len(world.execs) == 3 and {x["id"] for x in world.execs} == {
        "req13-error-before", "req13-error-primary", "req13-error-after"}
    assert PRIVATE not in json.dumps(report) and TOKEN not in json.dumps(report) and "body" not in json.dumps(report)


@pytest.mark.parametrize("damage", ["old-id", "unknown-id", "path", "origin", "query", "header", "auth", "rid",
    "token", "model", "prompt", "extra", "body-extra", "bool-zero", "stream", "max-tokens", "messages",
    "read-delay", "method", "mode", "want-body", "batch", "timeout", "network", "primary-first"])
def test_closed_request_rejects_before_any_remote_io(damage):
    value, world = transport()
    slot = "primary" if damage in {"stream", "max-tokens", "messages", "primary-first"} else "before"
    req = request(value, slot); timeout = 10; network = "kubernetes-probe"
    if damage == "old-id": req["id"] = "before"
    elif damage == "unknown-id": req["id"] = "req13-error-other"
    elif damage == "path": req["url"] = value.endpoint.geturl() + "/v1/completions"
    elif damage == "origin": req["url"] = "http://127.0.0.99:8000/tokenize"
    elif damage == "query": req["url"] += "?secret=private"
    elif damage == "header": req["headers"]["Other"] = PRIVATE
    elif damage == "auth": req["headers"]["Authorization"] = PRIVATE
    elif damage == "rid": req["headers"]["X-Request-Id"] += "-other"
    elif damage == "token": req["headers"]["X-Request-Id"] = "byul-req13-error-invalid-before"
    elif damage == "model": req["body"]["model"] = env().model.id
    elif damage == "prompt": req["body"]["prompt"] = PRIVATE
    elif damage == "extra": req["verified"] = True
    elif damage == "body-extra": req["body"]["request_id"] = PRIVATE
    elif damage == "bool-zero": req["body"]["return_token_strs"] = 0
    elif damage == "stream": req["body"]["stream"] = True
    elif damage == "max-tokens": req["body"]["max_tokens"] = 1
    elif damage == "messages": req["body"]["messages"][0]["content"] = PRIVATE
    elif damage == "read-delay": req["read_delay_ms"] = 1
    elif damage == "method": req["method"] = "GET"
    elif damage == "mode": req["mode"] = "sse"
    elif damage == "want-body": req["want_body"] = False
    elif damage == "timeout": timeout = 121
    elif damage == "network": network = "other"
    with pytest.raises(kube.KubernetesBindingError):
        value(network, [req, deepcopy(req)] if damage == "batch" else [req], timeout)
    assert not world.calls and not world.execs and value.report()["collection_gap"]
    assert PRIVATE not in json.dumps(value.report())


@pytest.mark.parametrize("profile", [kube.PROFILE, kube.REQUEST_BINDING_PROFILE])
def test_existing_profiles_do_not_accept_req13_ids(profile):
    world = World()
    value = kube.KubernetesHTTP(world.target, env(), duration=60, runner=world.runner, profile=profile)
    if profile == kube.PROFILE:
        # Sampling does not expose API-model mapping: use an explicitly constructed REQ13-shaped request.
        value.api_model = "fixture-model"
    with pytest.raises(kube.KubernetesBindingError): send(value, "before")
    assert not world.calls and not world.execs


@pytest.mark.parametrize("slot", ["primary", "after", "before"])
def test_request_token_and_sequence_are_not_reusable(slot):
    value, world = transport()
    send(value, "before")
    if slot == "before": req = request(value, "before")
    else: req = request(value, slot, token="cd" * 16)
    with pytest.raises(kube.KubernetesBindingError): value("kubernetes-probe", [req], 10)
    assert len(world.execs) == 1


def test_after_may_follow_bad_normal_without_primary_and_closes_sequence():
    value, world = transport()
    send(value, "before"); send(value, "after")
    with pytest.raises(kube.KubernetesBindingError): send(value, "primary")
    assert len(world.execs) == 2


@pytest.mark.parametrize("role", ["engine", "probe"])
@pytest.mark.parametrize("field", ["pod_uid", "container_id", "image_id", "restart_count", "started_at"])
def test_before_after_both_generations_gate_response(role, field):
    value, world = transport()
    def mutate(which, row, after):
        if which != role or not after: return
        if field == "pod_uid": row["metadata"]["uid"] = "replaced"
        elif field == "started_at": row["status"]["containerStatuses"][0]["state"]["running"]["startedAt"] = "replaced"
        else: row["status"]["containerStatuses"][0][{"container_id": "containerID", "image_id": "imageID", "restart_count": "restartCount"}[field]] = 1
    world.mutate = mutate
    with pytest.raises(kube.KubernetesBindingError): send(value, "before")
    assert len(world.execs) == 1 and not value.report()["requests"][0]["binding_verified_before_after"]


@pytest.mark.parametrize("damage", ["wrong-id", "oversized", "timeout"])
def test_failed_wire_response_has_only_fixed_gap(damage):
    value, world = transport(World(damage=damage))
    with pytest.raises(kube.KubernetesBindingError): send(value, "before")
    assert value.report()["collection_gap"] and PRIVATE not in json.dumps(value.report())


def test_primary_facts_survive_later_cleanup_or_binding_failure():
    value, world = transport()
    send(value, "before"); primary, = send(value, "primary")
    world.damage = "cleanup"; after, = send(value, "after")
    assert primary["outcome"] == 400 and after["outcome"] == 200
    assert value.report()["cleanup_errors"] == ["KUBERNETES_PROBE_PARENT_CLEANUP_UNVERIFIED"]
    assert PRIVATE not in json.dumps(value.report())


def test_partial_response_remains_transient_and_explicitly_incomplete():
    value, world = transport(World(damage="partial"))
    row, = send(value, "before")
    assert not row["capture_complete"] and value.report()["collection_gap"]


@pytest.mark.parametrize("damage", ["version", "env-ref", "model", "image"])
def test_constructor_eligibility_is_checked_before_io(damage):
    environment = env(); world = World()
    if damage == "version":
        environment.engine.version = "0.32.0"; environment = environment.seal()
        world.target["environment_ref"] = environment.ref().model_dump(mode="json")
    elif damage == "env-ref": world.target["environment_ref"]["sha256"] = "0" * 64
    elif damage == "model": world.target["model_mapping"]["repository_id"] = "other"
    else: world.target["kubernetes"]["engine"]["image"] = "latest"
    with pytest.raises(kube.KubernetesBindingError):
        kube.KubernetesHTTP(world.target, environment, duration=60, runner=world.runner, profile=kube.VALIDATION_ERROR_PROFILE)
    assert not world.calls and not world.execs


def test_pinned_stdlib_probe_real_loopback_json_400_and_cleanup():
    def handle(conn, raw, index, stop):
        head, _, payload = raw.partition(b"\r\n\r\n")
        body = json.loads(payload)
        primary = head.startswith(b"POST /v1/chat/completions ")
        if primary:
            assert body == {"model": "fixture-model", "messages": [{"role": "user", "content": "hello"}],
                            "max_tokens": "b2error-" + TOKEN[:12], "stream": False}
        text = json.dumps({"error": {"message": PRIVATE}} if primary else
                          {"tokens": [1, 2], "count": 2, "max_model_len": 128}).encode()
        status = b"400 Bad Request" if primary else b"200 OK"
        conn.sendall(b"HTTP/1.1 " + status + b"\r\nContent-Type: application/json\r\nContent-Length: "
                     + str(len(text)).encode() + b"\r\nConnection: close\r\n\r\n" + text)
    with _server(handle) as (url, accepted):
        target_ = target(); target_["engine"]["trusted_url"] = url
        world = World(target_)
        def runner(argv, **kwargs):
            if "exec" in argv:
                return capture_pipes([sys.executable, "-c", argv[-2], argv[-1]], **kwargs)
            return world.runner(argv, **kwargs)
        value = kube.KubernetesHTTP(target_, env(), duration=30, runner=runner, profile=kube.VALIDATION_ERROR_PROFILE)
        responses = [send(value, slot)[0] for slot in ("before", "primary", "after")]
        assert len(accepted) == 3
    assert [r["outcome"] for r in responses] == [200, 400, 200]
    assert all(r["capture_complete"] and r["eof"] and r["utf8_valid"] for r in responses)
    assert all(hashlib.sha256(r["body"].encode()).hexdigest() == r["body_sha256"] for r in responses)
    assert not value.report()["cleanup_errors"] and PRIVATE not in json.dumps(value.report())
