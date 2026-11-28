import json
import os

import pytest

from owned_startup_trace import StartupTrace, digest, startup_actor


def test_stock_entrypoint_and_child_selection():
    assert startup_actor(["python3", "/usr/local/bin/vllm", "serve", "/models"])
    assert startup_actor(["python3", "-c", "from multiprocessing.spawn import spawn_main; spawn_main(tracker_fd=2, pipe_handle=3)"])
    assert startup_actor(["python3", "-c", "print('not the stock command')"]) is None


@pytest.mark.parametrize("exception", [False, True])
def test_exact_runtime_entry_and_return_or_unwind(tmp_path, exception):
    source = tmp_path / "mock_stock.py"
    raw = b"def hook():\n    return None\n" if not exception else b"def hook():\n    raise ValueError('fixture')\n"
    source.write_bytes(raw)
    path = str(source)
    symbols = {}
    exec(compile(raw, path, "exec", dont_inherit=True), symbols)
    profile = {"session": "owned-test", "sources": {path: {"sha256": digest(raw),
               "qualnames": {"hook": {"role": "registration-callback", "entry_fingerprint": "e" * 64}}}}}
    log = tmp_path / "result.jsonl"
    with log.open("wb") as output:
        trace = StartupTrace(profile, output.fileno(), "owned-fixture")
        trace.arm()
        try:
            if exception:
                with pytest.raises(ValueError, match="fixture"):
                    symbols["hook"]()
            else:
                assert symbols["hook"]() is None
        finally:
            trace.release()
    identity, call, end = (json.loads(line) for line in log.read_bytes().splitlines())
    assert call["kind"] == "enter" and call["entry_fingerprint"] == "e" * 64
    assert end["kind"] == ("unwind" if exception else "return")
    assert end["call"] == call["seq"] and call["pid"] == end["pid"] == identity["pid"] == os.getpid()
    assert end["result"] == (None if exception else {"return_kind": "none"})


def test_registry_snapshot_records_insertion_without_reading_resolver_values():
    from types import SimpleNamespace
    from owned_startup_trace import registry_event

    cls = type("_LoRAResolverRegistry", (), {"__module__":"vllm.lora.resolver"})
    r = cls()
    r.resolvers = {}
    candidate = object()
    frame = SimpleNamespace(f_locals={"self":r,"resolver_name":"Filesystem Resolver","resolver":candidate})
    assert registry_event(frame) == {"approved_registry_label":"Filesystem Resolver","entry_present":False,
        "registry_count":0,"selected_instance_is_call_argument":False}
    r.resolvers["Filesystem Resolver"] = candidate
    assert registry_event(frame) == {"approved_registry_label":"Filesystem Resolver","entry_present":True,
        "registry_count":1,"selected_instance_is_call_argument":True}
