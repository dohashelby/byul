"""Exact 0.7 V1 source bodies into the existing cache/slot consumers; EXAMPLE only."""
from collections import namedtuple
import json
from pathlib import Path
from types import CodeType, FunctionType, ModuleType, SimpleNamespace

from guard.adapters import vllm_cache_callback as cache
from guard.adapters import vllm_engine_callback_sensor as sensor
from guard.adapters.vllm_observer_pin_rows import BASELINE_ROWS
from guard.executors import engine_callback_projection as projection
from guard.models import sha256_hex


ROOT = Path(__file__).parent / "fixtures/vllm-0.7.0/vllm"
SECRET = b"EXAMPLE-baseline-cache-slot-key-01"


def source_module(name, points, pins, tmp_path):
    relative = name.removeprefix("vllm.").replace(".", "/") + ".py"
    raw = (ROOT / (relative + ".txt")).read_bytes()
    assert sha256_hex(raw) == BASELINE_ROWS[name]["sha256"]
    assert sha256_hex(raw) in cache._pins(pins, name)
    path = tmp_path / (name.rsplit(".", 1)[-1] + ".py")
    path.write_bytes(raw)
    module = ModuleType(name)
    module.__file__ = str(path)
    codes = {}
    def visit(code):
        codes[code.co_qualname] = code
        for child in code.co_consts:
            if type(child) is CodeType:
                visit(child)
    visit(compile(raw, str(path), "exec", dont_inherit=True, optimize=0))
    for point in points:
        owner, method = point.split(".")
        cls = vars(module).setdefault(owner, type(owner, (), {"__module__": name}))
        setattr(cls, method, FunctionType(codes[point], vars(module)))
    return module


def timing_cases():
    return [{"case_id": name, "role": role} for name, role in (
        ("control-present", "control-present"), ("control-absent", "control-absent"),
        ("present", "present"), ("absent", "absent"))]


def test_baseline_flat_kv_return_reaches_computed_rows_and_cache_timing(tmp_path):
    name = "vllm.v1.core.kv_cache_manager"
    point = "KVCacheManager.get_computed_blocks"
    module = source_module(name, (point,), cache.SOURCES, tmp_path)
    observer = cache.CacheCallbackObserver("a" * 32, context_provider=lambda: {"pid": 1})
    observer.secret = SECRET
    module.KVCacheManager.get_computed_blocks = observer.wrap(module, point)
    manager = module.KVCacheManager()
    manager.enable_caching, manager.block_size = True, 4
    block = SimpleNamespace(block_id=7)
    BlockHashType = namedtuple("BlockHashType", "hash_value token_ids extra_keys")
    hashes = [BlockHashType(123, (3, 4, 5, 6), None)]
    tag = "byul-cb-" + "1" * 32
    request = SimpleNamespace(request_id=tag, kv_block_hashes=hashes, _kv_block_hashes=hashes,
                              lora_request=None)
    manager._get_cached_block = lambda value: block if value == hashes[0] else None
    assert manager.get_computed_blocks(request) == ([block], 4)
    control_tag = "byul-cb-" + "5" * 32
    control_request = SimpleNamespace(request_id=control_tag, kv_block_hashes=hashes,
                                      _kv_block_hashes=hashes, lora_request=None)
    assert manager.get_computed_blocks(control_request) == ([block], 4)
    events = observer.report()["events"]
    returned = [event for event in events if event["phase"] == "RETURN"]
    assert len(returned) == 2 and all(row["entry_qualified_before"] and row["entry_qualified_after"] for row in returned)
    fields = returned[0]["fields"]
    assert fields["num_computed_tokens"] == 4 and fields["shared_prefix_boundary"] is None
    assert fields["returned_group_blocks"] is None and len(fields["returned_flat_blocks"]) == 1
    assert fields["request_block_hashes_sha256"] == [observer.fingerprint("123")]
    assert fields["request_cache_salt_sha256"] is None and fields["request_media_ranges"] is None
    assert "byul-cb-" not in json.dumps(observer.report()) and "(3, 4, 5, 6)" not in json.dumps(observer.report())
    # The observer reads Request.__dict__['_kv_block_hashes']; it must not
    # call the public ConstantList property or inspect token IDs.
    class PropertyTrap:
        def __init__(self):
            self.__dict__.update(request.__dict__)
        @property
        def kv_block_hashes(self):
            raise AssertionError("observer must not invoke this property")
    assert observer._snapshot(point, (manager, PropertyTrap()), {})["request_block_hashes_sha256"] == [observer.fingerprint("123")]
    rows = projection.computed_rows(events, SECRET, {"present": tag, "control-present": control_tag})
    expected = {"tokens": 4, "hashes": [observer.fingerprint("123")], "salt": None, "media": None}
    assert rows == {"present": [expected], "control-present": [expected]}
    elapsed = {"control-present": 1, "control-absent": 10, "present": 1, "absent": 10}
    policy = {"cache_timing_max_auc_percent": 50, "cache_timing_min_samples": 1}
    result = projection.project_cache_timing(timing_cases(), rows, elapsed, policy)
    assert result["native_cross_hits"] == 1 and result["violation"] is True
    missing_control = projection.project_cache_timing(timing_cases(), {"present": rows["present"]}, elapsed, policy)
    assert missing_control["violation"] is None and not missing_control["full_scope"]
    assert "timing_control_native_hit_unverified" in missing_control["missing"]
    # A changed installed file cannot supply a qualified return to this consumer.
    Path(module.__file__).write_bytes(b"# changed source")
    assert manager.get_computed_blocks(request) == ([block], 4)
    unsupported = projection.computed_rows(observer.report()["events"][4:], SECRET, {"present": tag})
    assert unsupported == {}
    gap = projection.project_cache_timing(timing_cases(), unsupported, elapsed, policy)
    assert gap["violation"] is None and not gap["full_scope"]


def test_baseline_disabled_cache_cannot_complete_timing_even_with_fast_control(tmp_path):
    name = "vllm.v1.core.kv_cache_manager"
    point = "KVCacheManager.get_computed_blocks"
    module = source_module(name, (point,), cache.SOURCES, tmp_path)
    observer = cache.CacheCallbackObserver("c" * 32, context_provider=lambda: {"pid": 1})
    observer.secret = SECRET
    module.KVCacheManager.get_computed_blocks = observer.wrap(module, point)
    manager = module.KVCacheManager()
    manager.enable_caching = False  # 0.7 default: the pinned method returns before hash lookup.
    tags = {}
    for case in timing_cases():
        tag = "byul-cb-" + format(len(tags), "032x")
        tags[case["case_id"]] = tag
        request = SimpleNamespace(request_id=tag, _kv_block_hashes=[], lora_request=None)
        assert manager.get_computed_blocks(request) == ([], 0)
    rows = projection.computed_rows(observer.report()["events"], SECRET, tags)
    assert set(rows) == set(tags)
    assert all(row[0]["tokens"] == 0 for row in rows.values())
    elapsed = {"control-present": 1, "control-absent": 10, "present": 10, "absent": 10}
    policy = {"cache_timing_max_auc_percent": 50, "cache_timing_min_samples": 1}
    result = projection.project_cache_timing(timing_cases(), rows, elapsed, policy)
    assert result["control_auc_percent"] == 100
    assert result["violation"] is None and not result["full_scope"]
    assert "timing_control_native_hit_unverified" in result["missing"]
    assert "byul-cb-" not in json.dumps(observer.report())


class Matrix:
    def __setitem__(self, key, value):
        pass


def batch(module):
    value = module.InputBatch()
    value.max_num_reqs, value.num_reqs = 2, 0
    value.req_ids, value.req_id_to_index = [None, None], {}
    value.num_prompt_tokens = [0, 0]
    value.token_ids_cpu = Matrix()
    value.num_tokens = [0, 0]
    value.num_computed_tokens_cpu = [0, 0]
    value.block_table = SimpleNamespace(add_row=lambda *args: None)
    for name in ("temperature_cpu", "top_p_cpu", "top_k_cpu", "frequency_penalties_cpu",
                 "presence_penalties_cpu", "repetition_penalties_cpu", "min_tokens", "stop_token_ids"):
        setattr(value, name, [None, None])
    for name in ("greedy_reqs", "random_reqs", "top_p_reqs", "top_k_reqs",
                 "frequency_penalties_reqs", "presence_penalties_reqs", "repetition_penalties_reqs",
                 "prompt_logprob_reqs"):
        setattr(value, name, set())
    value.generators, value.num_logprobs = {}, {}
    return value


def request(req_id, allowed):
    params = SimpleNamespace(temperature=0.0, sampling_type="greedy", top_p=1.0, top_k=0,
        frequency_penalty=0.0, presence_penalty=0.0, repetition_penalty=1.0,
        min_tokens=0, all_stop_token_ids=[], logprobs=None, prompt_logprobs=None,
        allowed_token_ids=allowed)
    return SimpleNamespace(req_id=req_id, prompt_token_ids=[5], output_token_ids=[], num_tokens=1,
        num_computed_tokens=0, block_ids=[], sampling_params=params, generator=None)


def test_baseline_none_return_uses_verified_mapping_but_slot_consumer_keeps_movement_gap(tmp_path):
    name = sensor.SLOT_MODULE
    module = source_module(name, sensor.SLOT_TARGETS[name], sensor.SLOT_SOURCE, tmp_path)
    module.SamplingType = SimpleNamespace(GREEDY="greedy")
    # The in-pod observer streams to its sink instead of keeping report events.
    events = []
    observer = sensor.SlotObserver("b" * 32, sink=events.append, context_provider=lambda: {"pid": 1})
    observer.secret = SECRET
    for point in sensor.SLOT_TARGETS[name]:
        owner, method = point.split(".")
        setattr(vars(module)[owner], method, observer.wrap(module, point))
    value = batch(module)
    restricted = "byul-cb-" + "2" * 32
    reused = "byul-cb-" + "3" * 32
    assert value.add_request(request(restricted, [1]), 0) is None
    assert value.req_id_to_index[restricted] == 0
    assert value.remove_request(restricted) == 0
    assert value.add_request(request(reused, None), 0) is None
    returns = [row for row in events if row["phase"] == "RETURN"]
    assert [row["fields"]["slot"] for row in returns] == [0, 0, 0]
    assert returns[0]["fields"]["slot_lineage_complete"] is False
    assert returns[-1]["fields"]["slot_lineage_complete"] is False
    assert restricted not in json.dumps(returns) and reused not in json.dumps(returns)
    cases = [{"case_id": key, "role": role} for key, role in (
        ("alone-a", "standalone"), ("alone-b", "standalone"),
        ("restricted", "restricted"), ("reused", "reused"))]
    tags = {"restricted": restricted, "reused": reused}
    outputs = {"alone-a": "a", "alone-b": "a", "reused": "different"}
    result = projection.project_slot(cases, outputs, returns, SECRET, tags)
    assert result["runner"] == "v1" and not result["full_scope"] and result["violation"] is None
    assert result["rows"][0]["slot_reused"] is None and result["rows"][0]["slot_index_match_candidate"] is True
    assert "slot_movement_unobserved" in result["missing"]
    Path(module.__file__).write_bytes(b"# changed source")
    assert value.add_request(request("byul-cb-" + "4" * 32, None), 1) is None
    assert not any(row["phase"] == "RETURN" and row["entry_qualified_after"] is True
                   for row in events[-2:])
