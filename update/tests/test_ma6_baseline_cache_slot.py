"""Exact 0.7 V1 source bodies into the existing cache/slot consumers; EXAMPLE only."""
from collections import namedtuple
import json
from pathlib import Path
from types import CodeType, FunctionType, ModuleType, SimpleNamespace
import pytest

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
    def __getitem__(self, key):
        return []


def batch(module):
    value = module.InputBatch()
    value.max_num_reqs, value.num_reqs = 2, 0
    value.req_ids, value.req_id_to_index = [None, None], {}
    value.num_prompt_tokens = [0, 0]
    value.token_ids_cpu = Matrix()
    value.num_tokens = [0, 0]
    value.num_computed_tokens_cpu = [0, 0]
    value.block_table = SimpleNamespace(add_row=lambda *args: None, move_row=lambda *args: None)
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


def recording_slot_observer(session, *, context_provider):
    records = []
    def bounded_sink(row):
        if len(records) >= 256:
            raise AssertionError("EXAMPLE slot fixture event bound exceeded")
        records.append(row)
    observer = sensor.SlotObserver(session, sink=bounded_sink, context_provider=context_provider)
    observer.fixture_events = records
    return observer


def test_baseline_none_return_uses_verified_mapping_but_slot_consumer_keeps_movement_gap(tmp_path):
    name = sensor.SLOT_MODULE
    module = source_module(name, sensor.SLOT_TARGETS[name], sensor.SLOT_SOURCE, tmp_path)
    module.SamplingType = SimpleNamespace(GREEDY="greedy")
    observer = recording_slot_observer("b" * 32, context_provider=lambda: {"pid": 1})
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
    returns = [row for row in observer.fixture_events if row["phase"] == "RETURN"]
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
                   for row in observer.fixture_events[len(observer.fixture_events) - 2:])


def test_baseline_condense_tracks_real_movement_without_claiming_sampler_consumption(tmp_path):
    name = sensor.SLOT_MODULE
    points = sensor.SLOT_TARGETS[name] + sensor.BASELINE_SLOT_EXTRA
    module = source_module(name, points, sensor.SLOT_SOURCE, tmp_path)
    module.SamplingType = SimpleNamespace(GREEDY="greedy")
    observer = recording_slot_observer("d" * 32, context_provider=lambda: {"pid": 1})
    observer.secret = SECRET
    for point in points:
        owner, method = point.split(".")
        setattr(vars(module)[owner], method, observer.wrap(module, point))
    value = batch(module)
    restricted = "byul-cb-" + "6" * 32
    reused = "byul-cb-" + "7" * 32
    value.add_request(request(restricted, [1]), 0)
    value.add_request(request(reused, None), 1)
    assert value.remove_request(restricted) == 0
    value.num_reqs = 1
    empty = [0]
    assert value.condense(empty) is None
    assert value.req_id_to_index == {reused: 0} and value.req_ids == [reused, None]
    rows = observer.fixture_events
    movements = [row for row in rows if row["point"] == "InputBatch.condense"]
    assert len(movements) == 2 and all(row["entry_qualified_before"] for row in movements)
    assert movements[-1]["entry_qualified_after"] is True
    assert movements[0]["fields"]["slot_mapping"] == [
        {"request_sha256": observer.fingerprint(reused), "slot": 1}]
    assert movements[-1]["fields"]["slot_mapping"] == [
        {"request_sha256": observer.fingerprint(reused), "slot": 0}]
    cases = [{"case_id": key, "role": role} for key, role in (
        ("alone-a", "standalone"), ("alone-b", "standalone"),
        ("restricted", "restricted"), ("reused", "reused"))]
    tags = {"restricted": restricted, "reused": reused}
    outputs = {"alone-a": "a", "alone-b": "a", "reused": "different"}
    result = projection.project_slot(cases, outputs, rows, SECRET, tags)
    assert result["observed_slot_moves"] == 1
    assert result["rows"][0]["slot_index_match_candidate"] is True
    assert result["rows"][0]["slot_reused"] is None
    assert result["violation"] is None and not result["full_scope"]
    assert "slot_movement_unobserved" in result["missing"]  # final sampler consumption still absent
    assert restricted not in json.dumps(rows) and reused not in json.dumps(rows)
    assert value.clear() is None
    cleared = [row for row in observer.fixture_events if row["point"] == "InputBatch.clear"]
    assert len(cleared) == 2 and cleared[-1]["fields"]["slot_mapping"] == []
    Path(module.__file__).write_bytes(b"# changed source")
    assert value.condense([]) is None
    unqualified = observer.fixture_events[-2:]
    assert [row["phase"] for row in unqualified] == ["CALL", "RETURN"]
    assert all(row["entry_qualified_before"] is False for row in unqualified)
    assert "byul-cb-" not in json.dumps(observer.fixture_events)


def slot_world(tmp_path, name, *, context=None):
    directory = tmp_path / name
    directory.mkdir()
    module = source_module(sensor.SLOT_MODULE,
        sensor.SLOT_TARGETS[sensor.SLOT_MODULE] + sensor.BASELINE_SLOT_EXTRA, sensor.SLOT_SOURCE, directory)
    module.SamplingType = SimpleNamespace(GREEDY="greedy")
    observer = recording_slot_observer("e" * 32, context_provider=lambda: context or {"pid": 1, "start_ticks": 11})
    observer.secret = SECRET
    for point in sensor.SLOT_TARGETS[sensor.SLOT_MODULE] + sensor.BASELINE_SLOT_EXTRA:
        owner, method = point.split(".")
        setattr(vars(module)[owner], method, observer.wrap(module, point))
    return module, observer


def slot_projection(events, restricted, reused):
    cases = [{"case_id": key, "role": role} for key, role in (
        ("alone-a", "standalone"), ("alone-b", "standalone"),
        ("restricted", "restricted"), ("reused", "reused"))]
    return projection.project_slot(cases, {"alone-a": "same", "alone-b": "same", "reused": "different"},
        events, SECRET, {"restricted": restricted, "reused": reused})


def test_historical_reuse_candidate_survives_later_clear_and_new_allocations(tmp_path):
    module, observer = slot_world(tmp_path, "clear")
    value = batch(module)
    restricted, reused = "byul-cb-" + "8" * 32, "byul-cb-" + "9" * 32
    value.add_request(request(restricted, [1]), 0)
    value.remove_request(restricted)
    value.add_request(request(reused, None), 0)
    before = slot_projection(observer.fixture_events, restricted, reused)
    assert before["rows"][0]["slot_index_match_candidate"] is True
    value.clear()
    value.add_request(request("byul-cb-" + "a" * 32, None), 1)
    after = slot_projection(observer.fixture_events, restricted, reused)
    assert after["rows"] == before["rows"]
    assert after["violation"] is None and not after["full_scope"]
    assert after["rows"][0]["slot_reused"] is None


def test_historical_reuse_candidate_survives_later_move_to_another_slot(tmp_path):
    module, observer = slot_world(tmp_path, "move")
    value = batch(module)
    dummy = "byul-cb-" + "b" * 32
    restricted, reused = "byul-cb-" + "c" * 32, "byul-cb-" + "d" * 32
    value.add_request(request(dummy, None), 0)
    value.add_request(request(restricted, [1]), 1)
    value.remove_request(restricted)
    value.add_request(request(reused, None), 1)
    before = slot_projection(observer.fixture_events, restricted, reused)
    assert before["rows"][0]["slot_index_match_candidate"] is True
    value.remove_request(dummy)
    value.num_reqs = 1
    value.condense([0])
    assert value.req_id_to_index[reused] == 0
    after = slot_projection(observer.fixture_events, restricted, reused)
    assert after["rows"] == before["rows"]
    assert after["observed_slot_moves"] == 1
    assert after["violation"] is None and not after["full_scope"]


def test_equal_indices_in_distinct_batches_are_not_a_reuse_candidate(tmp_path):
    module, observer = slot_world(tmp_path, "batches")
    first, second = batch(module), batch(module)
    restricted, reused = "byul-cb-" + "0" * 32, "byul-cb-" + "1" * 32
    first.add_request(request(restricted, [1]), 0)
    first.remove_request(restricted)
    second.add_request(request(reused, None), 0)
    events = observer.fixture_events
    added = [row for row in events if row["point"] == "InputBatch.add_request" and row["phase"] == "RETURN"]
    assert added[0]["fields"]["batch_sha256"] != added[1]["fields"]["batch_sha256"]
    result = slot_projection(events, restricted, reused)
    assert "slot_index_match_candidate" not in result["rows"][0]
    assert result["rows"][0]["slot_reused"] is None and result["violation"] is None
    assert "slot_batch_process_binding_unavailable" in result["missing"]
    assert "byul-cb-" not in json.dumps(events)


@pytest.mark.parametrize('second_context', [{"pid": 72, "start_ticks": 11}, {"pid": 71, "start_ticks": 12}])
def test_process_and_start_generation_scope_cannot_join_even_colliding_batch_tokens(tmp_path, second_context):
    restricted, reused = "byul-cb-" + "2" * 32, "byul-cb-" + "3" * 32
    first_module, first_observer = slot_world(tmp_path, "process-one", context={"pid": 71, "start_ticks": 11})
    second_module, second_observer = slot_world(tmp_path, "process-two", context=second_context)
    # EXAMPLE namespace collision isolates the process/start-context check. All
    # event rows still come from the exact source producer, not hand fixtures.
    second_observer.slot_identity_namespace = first_observer.slot_identity_namespace
    first, second = batch(first_module), batch(second_module)
    first.add_request(request(restricted, [1]), 0)
    first.remove_request(restricted)
    second.add_request(request(reused, None), 0)
    events = first_observer.fixture_events + second_observer.fixture_events
    added = [row for row in events if row["point"] == "InputBatch.add_request" and row["phase"] == "RETURN"]
    assert added[0]["fields"]["batch_sha256"] == added[1]["fields"]["batch_sha256"]
    assert added[0]["process_sha256"] != added[1]["process_sha256"]
    result = slot_projection(events, restricted, reused)
    assert "slot_index_match_candidate" not in result["rows"][0]
    assert result["violation"] is None and not result["full_scope"]


def test_missing_batch_identity_and_repeated_tag_across_batches_remain_unknown(tmp_path):
    module, observer = slot_world(tmp_path, "ambiguous")
    first, second = batch(module), batch(module)
    restricted, reused = "byul-cb-" + "4" * 32, "byul-cb-" + "5" * 32
    first.add_request(request(restricted, [1]), 0)
    first.remove_request(restricted)
    first.add_request(request(reused, None), 0)
    assert slot_projection(observer.fixture_events, restricted, reused)["rows"][0]["slot_index_match_candidate"] is True
    second.add_request(request(reused, None), 0)
    result = slot_projection(observer.fixture_events, restricted, reused)
    assert "slot_index_match_candidate" not in result["rows"][0]
    assert result["violation"] is None
    third_module, third_observer = slot_world(tmp_path, "unbound")
    third_observer.token = lambda receiver: None  # actual producer unavailable-object path
    third = batch(third_module)
    third.add_request(request(restricted, [1]), 0)
    third.remove_request(restricted)
    third.add_request(request(reused, None), 0)
    unbound = slot_projection(third_observer.fixture_events, restricted, reused)
    assert "slot_index_match_candidate" not in unbound["rows"][0]
    assert unbound["violation"] is None and not unbound["full_scope"]


def test_clear_does_not_make_old_restricted_slot_available_in_a_new_batch_generation(tmp_path):
    module, observer = slot_world(tmp_path, "generation-clear")
    value = batch(module)
    restricted, reused = "byul-cb-" + "6" * 32, "byul-cb-" + "7" * 32
    value.add_request(request(restricted, [1]), 0)
    value.remove_request(restricted)
    value.clear()
    value.add_request(request(reused, None), 0)
    result = slot_projection(observer.fixture_events, restricted, reused)
    assert "slot_index_match_candidate" not in result["rows"][0]
    assert result["rows"][0]["slot_reused"] is None
    assert result["violation"] is None and not result["full_scope"]


def test_recreated_observers_do_not_reuse_batch_tokens_under_the_same_key_and_context(tmp_path):
    first_module, first_observer = slot_world(tmp_path, "observer-one")
    second_module, second_observer = slot_world(tmp_path, "observer-two")
    first, second = batch(first_module), batch(second_module)
    first.add_request(request("byul-cb-" + "8" * 32, None), 0)
    second.add_request(request("byul-cb-" + "9" * 32, None), 0)
    first_row = first_observer.fixture_events[-1]
    second_row = second_observer.fixture_events[-1]
    assert first_row["process_sha256"] == second_row["process_sha256"]
    assert first_row["fields"]["batch_sha256"] != second_row["fields"]["batch_sha256"]


@pytest.mark.parametrize('operation', ['clear', 'condense'])
@pytest.mark.parametrize('damage', ['drop-call', 'drop-return', 'invalid-map', 'raise', 'unqualified'])
def test_uncertain_movement_or_reset_cannot_supply_a_future_reuse_candidate(tmp_path, operation, damage):
    module, observer = slot_world(tmp_path, operation + '-' + damage)
    value = batch(module)
    restricted, reused = "byul-cb-" + "a" * 32, "byul-cb-" + "b" * 32
    survivor = "byul-cb-" + "c" * 32
    value.add_request(request(restricted, [1]), 0)
    if operation == 'condense':value.add_request(request(survivor, None), 1)
    value.remove_request(restricted)
    value.num_reqs = 1 if operation == 'condense' else 0
    boundary = len(observer.fixture_events)
    original_mapping = observer._mapping
    original_source = Path(module.__file__).read_bytes()
    if damage == 'invalid-map':
        calls = [0]
        def unavailable_after(receiver):
            calls[0] += 1
            return original_mapping(receiver) if calls[0] == 1 else None
        observer._mapping = unavailable_after
    if damage == 'unqualified':Path(module.__file__).write_bytes(b'# EXAMPLE changed source')
    error = RuntimeError('EXAMPLE partial mutation')
    if damage == 'raise':
        if operation == 'condense':
            def failed_move(*args):raise error
            value.block_table.move_row = failed_move
        else:
            class FailedClear(set):
                def clear(self):raise error
            value.greedy_reqs = FailedClear(value.greedy_reqs)
    if damage == 'raise':
        with pytest.raises(RuntimeError) as raised:
            value.clear() if operation == 'clear' else value.condense([0])
        assert raised.value is error
    else:
        assert (value.clear() if operation == 'clear' else value.condense([0])) is None
    observer._mapping = original_mapping
    if damage == 'unqualified':Path(module.__file__).write_bytes(original_source)
    boundary_rows = observer.fixture_events[boundary:]
    assert [row['phase'] for row in boundary_rows] == ['CALL', 'RAISE' if damage == 'raise' else 'RETURN']
    if damage == 'invalid-map':assert boundary_rows[-1]['fields']['slot_mapping'] is None
    if damage == 'unqualified':assert all(row['entry_qualified_before'] is False for row in boundary_rows)
    if operation == 'condense':
        # Even a failed condense moved the native mapping before our inert
        # collaborator raised. Remove that survivor before valid B allocation.
        assert value.req_id_to_index[survivor] == 0
        assert value.remove_request(survivor) == 0
    assert value.add_request(request(reused, None), 0) is None
    events = list(observer.fixture_events)
    if damage in {'drop-call', 'drop-return'}:
        phase = 'CALL' if damage == 'drop-call' else 'RETURN'
        selected = next(row for row in boundary_rows if row['phase'] == phase)
        events = [row for row in events if row is not selected]
    result = slot_projection(events, restricted, reused)
    assert 'slot_index_match_candidate' not in result['rows'][0]
    assert result['rows'][0]['slot_reused'] is None
    assert result['violation'] is None and not result['full_scope']
    assert 'slot_movement_or_reset_unavailable' in result['missing']


def test_uncertain_batch_binding_invalidates_the_process_without_erasing_prior_frozen_fact(tmp_path):
    module, observer = slot_world(tmp_path, 'unknown-batch')
    first, second = batch(module), batch(module)
    old_a, old_b = "byul-cb-" + "d" * 32, "byul-cb-" + "e" * 32
    new_a, new_b = "byul-cb-" + "f" * 32, "byul-cb-" + "0" * 32
    first.add_request(request(old_a, [1]), 0)
    first.remove_request(old_a)
    first.add_request(request(old_b, None), 0)
    prior = slot_projection(observer.fixture_events, old_a, old_b)
    assert prior['rows'][0]['slot_index_match_candidate'] is True
    second.add_request(request(new_a, [1]), 0)
    second.remove_request(new_a)
    original_token = observer.token
    observer.token = lambda receiver: None  # actual source producer cannot bind reset receiver
    first.clear()
    observer.token = original_token
    second.add_request(request(new_b, None), 0)
    result = slot_projection(observer.fixture_events, new_a, new_b)
    assert 'slot_index_match_candidate' not in result['rows'][0]
    assert result['violation'] is None and not result['full_scope']
    earlier = slot_projection(observer.fixture_events, old_a, old_b)
    assert earlier['rows'] == prior['rows']  # exact earlier batch/process identity remains proven
    assert 'slot_movement_or_reset_unavailable' in earlier['missing']


def test_untracked_condense_occupant_consumes_the_removed_slot_lease(tmp_path):
    module, observer = slot_world(tmp_path, 'untracked-occupant')
    value = batch(module)
    restricted, reused = "byul-cb-" + "1" * 32, "byul-cb-" + "2" * 32
    survivor = "byul-cb-" + "3" * 32
    value.add_request(request(survivor, None), 1)
    preexisting = list(observer.fixture_events)
    value.add_request(request(restricted, [1]), 0)
    value.remove_request(restricted)
    value.num_reqs = 1
    value.condense([0])
    assert value.req_id_to_index[survivor] == 0
    value.remove_request(survivor)
    value.add_request(request(reused, None), 0)
    # A source producer observed C's real movement, but the selected journal
    # began after C's allocation. C still consumed A's freed slot before B.
    events = [row for row in observer.fixture_events if all(row is not earlier for earlier in preexisting)]
    result = slot_projection(events, restricted, reused)
    assert result['observed_slot_moves'] == 1  # qualified maps count C even without its earlier allocation
    assert result['rows'][0]['slot_index_match_candidate'] is False
    assert result['rows'][0]['slot_reused'] is None
    assert result['violation'] is None and not result['full_scope']
