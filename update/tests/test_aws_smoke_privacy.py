"""Bootstrap helper contracts. EXAMPLE only; no kubectl, GPU, AWS or engine."""
from copy import deepcopy
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading

import pytest

from tools import aws_smoke_client as client, aws_smoke_privacy as privacy, prepare_aws_smoke as prep

PRIVATE = "EXAMPLE-private-body-header-env-exception"
RUN_ID = "guard-example-01"
IMAGE = "example/python@sha256:" + "a" * 64


def normal():
    return {"model": "test-model", "choices": [{"text": PRIVATE, "finish_reason": "length"}], "usage": {"completion_tokens": 2}}


def sse():
    return b'data: ' + json.dumps({"model": "test-model", "choices": [{"text": PRIVATE, "finish_reason": "length"}]}).encode() + b'\n\ndata: [DONE]\n\n'


def response():
    return {"schema": client.SCHEMA, "health_200": True, "normal": client.validate_normal(json.dumps(normal()).encode()),
            "stream": client.validate_stream(sse())}


@pytest.mark.parametrize("change", ["error", "model", "text", "finish", "usage", "boolean_usage", "malformed", "utf8"])
def test_normal_keeps_existing_content_finish_and_usage_checks(change):
    value = normal()
    if change == "error": value["error"] = PRIVATE
    elif change == "model": value["model"] = "other"
    elif change == "text": value["choices"][0]["text"] = ""
    elif change == "finish": value["choices"][0]["finish_reason"] = None
    elif change == "usage": value["usage"]["completion_tokens"] = 0
    elif change == "boolean_usage": value["usage"]["completion_tokens"] = True
    raw = b"bad" if change == "malformed" else b"\xff" if change == "utf8" else json.dumps(value).encode()
    with pytest.raises(client.SmokeError, match="^NORMAL_SCHEMA_UNVERIFIED$"):
        client.validate_normal(raw)


@pytest.mark.parametrize("change", ["missing_done", "early_done", "model", "no_text", "no_finish", "error", "malformed", "utf8"])
def test_sse_requires_every_model_content_finish_and_done_last(change):
    raw = sse()
    if change == "missing_done": raw = raw.replace(b"data: [DONE]\n\n", b"")
    elif change == "early_done": raw = b"data: [DONE]\n\n" + raw
    elif change == "model": raw = raw.replace(b"test-model", b"other-model")
    elif change == "no_text": raw = raw.replace(PRIVATE.encode(), b"")
    elif change == "no_finish": raw = raw.replace(b'"length"', b"null")
    elif change == "error": raw = raw.replace(b'"model":', b'"error":true,"model":')
    elif change == "malformed": raw = b"data: not-json\n\ndata: [DONE]\n\n"
    else: raw = b"\xff" + raw
    with pytest.raises(client.SmokeError, match="^SSE_SCHEMA_UNVERIFIED$"):
        client.validate_stream(raw)


def test_client_emits_only_safe_summary_and_preserves_request_recipe(monkeypatch, capsys):
    calls = []
    def request(ip, path, body, deadline):
        calls.append((ip, path, deepcopy(body), deadline))
        return b"health" if body is None else sse() if body.get("stream") else json.dumps(normal()).encode()
    monkeypatch.setattr(client, "request", request)
    assert client.main(["10.0.0.2"]) == 0
    output = capsys.readouterr().out
    assert PRIVATE not in output and "Say hello" not in output
    assert privacy.validate_result(output) == response()
    assert [c[3] for c in calls] == [30, 120, 120]
    assert calls[1][2] == {"model": "test-model", "prompt": "Say hello.", "max_tokens": 8}
    assert calls[2][2] == {**calls[1][2], "stream": True}


def test_client_exception_never_echoes_dependency_text(monkeypatch, capsys):
    monkeypatch.setattr(client, "request", lambda *_: (_ for _ in ()).throw(RuntimeError(PRIVATE)))
    assert client.main(["10.0.0.2"]) == 1
    assert PRIVATE not in capsys.readouterr().out


def pod():
    return {"kind": "Pod", "metadata": {"name": "engine", "namespace": "serving-lab", "uid": "fixture-uid",
            "resourceVersion": "1", "annotations": {"private": PRIVATE}, "labels": {"app": PRIVATE, "guard.run": RUN_ID}},
            "spec": {"nodeName": "byul-gpu-01", "containers": [{"name": "engine", "image": IMAGE,
                "env": [{"value": PRIVATE}], "command": [PRIVATE], "args": [PRIVATE]}]},
            "status": {"phase": "Running", "message": PRIVATE, "containerStatuses": [{"name": "engine", "ready": True,
                "imageID": IMAGE, "containerID": "containerd://" + "b" * 64, "restartCount": 0,
                "state": {"waiting": {"reason": "ImagePullBackOff", "message": PRIVATE}}}]}}


@pytest.mark.parametrize("kind", ["Pod", "Deployment", "Job", "Event", "NetworkPolicy", "Service", "Node"])
def test_projection_never_copies_raw_specs_annotations_messages_or_env(kind):
    value = pod(); value["kind"] = kind
    if kind in {"Deployment", "Job"}: value["spec"] = {"template": {"spec": value["spec"]}}
    if kind == "Node": value["status"]["nodeInfo"] = {"kernelVersion": "6.8", "private": PRIVATE}
    value["message"] = PRIVATE
    value["reason"] = PRIVATE
    result = privacy.project(value)
    assert PRIVATE not in json.dumps(result)
    assert result["metadata"]["uid"] == "fixture-uid" and len(result["source_canonical_sha256"]) == 64


@pytest.mark.parametrize("target,mode,raw", [("job/engine-smoke", "normal", b"raw private reply"),
    ("job/gpu-smoke", "gpu", b"No compute passed"), ("job/engine-denied", "denied", b"HTTP 403")])
def test_bad_or_missing_observations_cannot_become_smoke_success(monkeypatch, target, mode, raw):
    monkeypatch.setattr(privacy, "call", lambda *_, **__: {"stdout_bytes": raw})
    with pytest.raises(privacy.CaptureError): privacy.logs(target, mode)


def test_unselected_log_fields_and_raw_native_log_are_not_written(monkeypatch):
    value = {**response(), "private": PRIVATE}
    monkeypatch.setattr(privacy, "call", lambda *_, **__: {"stdout_bytes": json.dumps(value).encode()})
    assert PRIVATE not in json.dumps(privacy.logs("job/engine-smoke", "normal"))
    monkeypatch.setattr(privacy, "call", lambda *_, **__: {"stdout_bytes": PRIVATE.encode()})
    assert PRIVATE not in json.dumps(privacy.logs("deployment/serving-engine", "opaque"))


def test_native_call_failure_uses_fixed_diagnostic(monkeypatch):
    monkeypatch.setattr(privacy, "capture_pipes", lambda *_, **__: (_ for _ in ()).throw(RuntimeError(PRIVATE)))
    with pytest.raises(privacy.CaptureError, match="^KUBERNETES_CAPTURE_UNAVAILABLE$"):
        privacy.call(["get", "pods"])


@pytest.mark.parametrize("field", ["containers", "initContainers"])
def test_active_gpu_owner_is_not_removed_or_ignored(field):
    value = pod(); value["spec"][field] = [{"resources": {"limits": {"nvidia.com/gpu": "1"}}}]
    with pytest.raises(privacy.CaptureError, match="ACTIVE_GPU_OWNER_PRESENT"):
        privacy.preflight({"items": [value]})
    value["status"]["phase"] = "Succeeded"
    privacy.preflight({"items": [value]})


@pytest.mark.parametrize("mode", ["revoke", "engine-smoke"])
def test_cleanup_tests_uid_version_and_owner_before_atomic_patch(monkeypatch, mode):
    value = {"metadata": {"uid": "exact-uid", "resourceVersion": "18", "labels": {"guard.run": RUN_ID}},
             "spec": {"ingress": [{"from": []}]}, "status": {"conditions": [{"type": "Complete", "status": "True"}]}}
    if mode == "revoke": value["spec"].update(privacy.render_policy(RUN_ID)["spec"])
    calls = []
    def call(args, **kwargs):
        calls.append(args)
        if "get" in args: return {"stdout_bytes": json.dumps(value).encode()}
        if "patch" in args:
            patch = json.loads(args[args.index("-p") + 1])
            assert patch[:3] == [{"op": "test", "path": "/metadata/uid", "value": "exact-uid"},
                {"op": "test", "path": "/metadata/resourceVersion", "value": "18"},
                {"op": "test", "path": "/metadata/labels/guard.run", "value": RUN_ID}]
            assert patch[-1]["value"] == ([] if mode == "revoke" else 0)
            return {**value, "spec": {**value["spec"], "ingress": []}}
        return {"stdout_bytes": b""}
    monkeypatch.setattr(privacy, "call", call)
    result = privacy.owned_mutation(mode, RUN_ID)
    assert result["uid"] == "exact-uid"
    assert not any("delete" in c for c in calls)


def test_other_owner_is_never_patched(monkeypatch):
    calls = []
    def call(args, **kwargs):
        calls.append(args)
        return {"stdout_bytes": json.dumps({"metadata": {"uid": "other", "labels": {"guard.run": "other"}}}).encode()}
    monkeypatch.setattr(privacy, "call", call)
    with pytest.raises(privacy.CaptureError, match="OWNERSHIP_UNVERIFIED"): privacy.owned_mutation("revoke", RUN_ID)
    assert len(calls) == 1


@pytest.mark.parametrize("shape", ["omitted", "empty", "null", "nonempty", "selector", "type", "owner", "uid"])
def test_revoke_typed_api_empty_list_and_scope_changes(monkeypatch, shape):
    before=privacy.render_policy(RUN_ID)
    before["metadata"].update(uid="exact-uid",resourceVersion="18")
    after=deepcopy(before);after["spec"].pop("ingress")
    if shape=="empty":after["spec"]["ingress"]=[]
    elif shape=="null":after["spec"]["ingress"]=None
    elif shape=="nonempty":after["spec"]["ingress"]=[{}]
    elif shape=="selector":after["spec"]["podSelector"]={}
    elif shape=="type":after["spec"]["policyTypes"]=["Egress"]
    elif shape=="owner":after["metadata"]["labels"]["guard.run"]="other"
    elif shape=="uid":after["metadata"]["uid"]="replacement"
    calls=[]
    def call(args,**kwargs):
        calls.append(args)
        return {"stdout_bytes":json.dumps(before).encode()} if "get" in args else after
    monkeypatch.setattr(privacy,"call",call)
    if shape in {"omitted","empty"}:
        result=privacy.owned_mutation("revoke",RUN_ID)
        assert result["temporary_access_revoked"] is True and result["deny_only_policy_retained"] is True
    else:
        with pytest.raises(privacy.CaptureError,match="OWNED_MUTATION_UNVERIFIED"):
            privacy.owned_mutation("revoke",RUN_ID)
    assert len(calls)==2 and "patch" in calls[-1]


@pytest.mark.parametrize("field,value",[("policyTypes",["Egress"]),("podSelector",{})])
def test_revoke_refuses_foreign_policy_scope_before_mutation(monkeypatch,field,value):
    before=privacy.render_policy(RUN_ID);before["metadata"].update(uid="exact-uid",resourceVersion="18")
    before["spec"][field]=value;calls=[]
    def call(args,**kwargs):
        calls.append(args);return {"stdout_bytes":json.dumps(before).encode()}
    monkeypatch.setattr(privacy,"call",call)
    with pytest.raises(privacy.CaptureError,match="OWNED_POLICY_SCOPE_UNVERIFIED"):
        privacy.owned_mutation("revoke",RUN_ID)
    assert len(calls)==1 and "get" in calls[0]


@pytest.mark.parametrize("denied", [False, True])
def test_rendered_client_has_pinned_image_no_service_token_and_same_denied_role(denied):
    value = privacy.render_job(IMAGE, "10.0.0.2", RUN_ID, denied=denied)
    spec = value["spec"]["template"]["spec"]
    assert spec["automountServiceAccountToken"] is False
    assert spec["serviceAccountName"] == "probe" and spec["containers"][0]["image"] == IMAGE
    assert value["metadata"]["labels"]["guard.run"] == RUN_ID
    assert value["spec"]["template"]["metadata"]["labels"]["app"] == "engine-test-probe"
    if denied:
        command = spec["containers"][0]["command"][-1]
        assert 'test "$result" -eq 28' in command and "--max-time 8" in command and "2>/dev/null" in command


def test_original_validator_output_and_failure_text_never_escape(tmp_path, monkeypatch, capsys):
    path = tmp_path / "validator.py"
    source = ("import sys\nprint(" + repr(PRIVATE) + ")\nraise SystemExit(" + repr(PRIVATE) + ")\n").encode()
    path.write_bytes(source)
    monkeypatch.setattr(privacy, "BASELINE_SHA", privacy.digest(source))
    before = sys.argv
    with pytest.raises(privacy.CaptureError, match="^FOUNDATION_BASELINE_UNVERIFIED$"):
        privacy.legacy_baseline({}, {}, path)
    assert sys.argv is before and PRIVATE not in capsys.readouterr().out


def test_unknown_source_or_output_overlap_is_rejected_without_writes(tmp_path):
    source = tmp_path / "input"; source.mkdir()
    (source / "SHA256SUMS").write_text("not reviewed", encoding="utf-8")
    output = tmp_path / "output"
    with pytest.raises(ValueError, match="UNREVIEWED_HANDOFF_MANIFEST"): prep.prepare(source, output)
    assert not output.exists()
    with pytest.raises(ValueError, match="NEW_EXTERNAL_DESTINATION_REQUIRED"): prep.prepare(source, source / "nested")


def test_failed_preflight_preserves_safe_observation_and_fixed_diagnostic(tmp_path, monkeypatch, capsys):
    value = pod(); value["spec"]["containers"][0]["resources"] = {"limits": {"nvidia.com/gpu": "1"}}
    monkeypatch.setattr(privacy, "call", lambda *_: {"items": [value]})
    out = tmp_path / "partial.json"
    assert privacy.main(["capture", "--kind", "preflight", "--output", str(out)]) == 1
    raw = out.read_text(encoding="utf-8")
    result = json.loads(raw)
    assert result["complete"] is False and result["diagnostic"] == "ACTIVE_GPU_OWNER_PRESENT"
    assert result["observed"]["items"][0]["metadata"]["uid"] == "fixture-uid"
    assert PRIVATE not in raw + capsys.readouterr().out


def test_partial_pipe_capture_keeps_bytes_hash_without_raw_stdout(monkeypatch):
    monkeypatch.setattr(privacy, "capture_pipes", lambda *_, **__: {"returncode": 1,
        "stdout_bytes": PRIVATE.encode(), "capture": {"complete": False}})
    with pytest.raises(privacy.CaptureError) as exc:
        privacy.call(["get", "pods"])
    assert exc.value.record["retained_sha256"] == privacy.digest(PRIVATE.encode())
    assert PRIVATE not in json.dumps(exc.value.record)


def test_client_real_owned_http_roundtrip_never_prints_reply(monkeypatch, capsys):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_GET(self):
            self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(200); self.end_headers()
            self.wfile.write(sse() if body.get("stream") else json.dumps(normal()).encode())
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    original = client.http.client.HTTPConnection
    monkeypatch.setattr(client.http.client, "HTTPConnection", lambda *_args, **kwargs: original("127.0.0.1", server.server_port, **kwargs))
    try:
        assert client.main(["10.0.0.2"]) == 0
        output = capsys.readouterr().out
        assert PRIVATE not in output
        assert privacy.validate_result(output) == response()
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_client_body_limit_rejects_truncated_large_response(monkeypatch):
    class Response:
        status = 200
        def read(self, count):
            assert count == client.MAX_BODY + 1
            return b"x" * count
    class Connection:
        def request(self, *_, **__): pass
        def getresponse(self): return Response()
        def close(self): pass
    monkeypatch.setattr(client.http.client, "HTTPConnection", lambda *_, **__: Connection())
    with pytest.raises(client.SmokeError, match="^HTTP_OR_BODY_UNVERIFIED$"):
        client.request("10.0.0.2", "/health", None, 1)


def test_client_watchdog_ends_own_blocked_process():
    code = "import runpy,time\nv=runpy.run_path(" + repr(str(Path(client.__file__))) + ")\n" + '''
class Response:
    status=200
    def read(self,*args): time.sleep(20)
class Connection:
    def request(self,*args,**kwargs): pass
    def getresponse(self): return Response()
    def close(self): pass
v['http'].client.HTTPConnection=lambda *args,**kwargs:Connection()
v['request']('10.0.0.2','/health',None,0.2)
'''
    from guard.runtime.bounded import capture_pipes
    result = capture_pipes([sys.executable, "-c", code], timeout=3, env_extra={}, max_stdout_bytes=4096, max_stderr_bytes=4096)
    assert result["returncode"] == 124 and result["capture"]["complete"]
    assert not result["stdout_bytes"]


def test_prepared_helper_runs_without_guard_or_site_packages(tmp_path):
    from guard.runtime import bounded
    for module in (client, privacy): shutil.copy2(module.__file__, tmp_path / Path(module.__file__).name)
    shutil.copy2(bounded.__file__, tmp_path / "bounded_capture.py")
    output = tmp_path / "client.json"
    result = subprocess.run([sys.executable, "-S", str(tmp_path / "aws_smoke_privacy.py"), "render-client", "--image", IMAGE,
        "--engine-ip", "10.0.0.2", "--run-id", RUN_ID, "--output", str(output)], cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": "", "PYTHONIOENCODING": "utf-8"}, capture_output=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert json.loads(output.read_text(encoding="utf-8"))["spec"]["template"]["spec"]["automountServiceAccountToken"] is False
