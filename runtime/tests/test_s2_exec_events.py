from types import SimpleNamespace
from tests.test_s2_exec import source
from serving_runtime.enforcement.intent import parse_intent
from serving_runtime.enforcement.events import parse_tetragon


def event(intent):
    return {
        "time": "2026-11-16T00:00:00Z",
        "process_lsm": {
            "function_name": "bprm_check_security",
            "policy_name": "runtime-tg-exec-01",
            "action": "KPROBE_ACTION_OVERRIDE",
            "process": {
                "binary": "/usr/bin/python3",
                "pod": {
                    "namespace": intent.namespace,
                    "name": "engine-demo",
                    "pod_labels": intent.engine.labels,
                    "container": {"name": intent.engine.container_name or "engine"},
                },
            },
            "args": [{"linux_binprm_arg": {"path": "/bin/sh"}}],
        },
    }


def test_exec_lsm_is_attempt_not_success_and_requires_container_attribution():
    intent = parse_intent(source())
    raw = event(intent)
    normalized = parse_tetragon(raw, data_origin="synthetic", exec_policy=SimpleNamespace(intent=intent))
    assert normalized["exec_verdict"] == "deny_attempted"
    assert normalized["reason"] == "exec_denied_by_deny_list"
    assert normalized["action_mode"] == "enforce"
    assert normalized["binary"] == "/bin/sh"
    assert normalized["caller_binary"] == "/usr/bin/python3"
    assert normalized["engine_attribution"] == "confirmed"
    raw["process_lsm"]["process"]["pod"].pop("container")
    normalized = parse_tetragon(raw, data_origin="synthetic", exec_policy=SimpleNamespace(intent=intent))
    assert normalized["engine_attribution"] == "engine_attribution_unavailable"
    assert normalized["exec_verdict"] == "deny_attempted"
    assert "verification" not in normalized
