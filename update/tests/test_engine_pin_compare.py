"""M-A6 판본 고정값 비교 도구의 직접 시험. 합성 소스 트리만 쓰고 엔진을 import하지 않는다."""
from __future__ import annotations

import json

import pytest

from tools import engine_pin_compare as tool

REFERENCE = {
    "vllm/__init__.py": "",
    "vllm/core.py": (
        "class Engine:\n"
        "    def step(self, x):\n"
        "        \"\"\"Run one step.\"\"\"\n"
        "        return x + 1\n"
        "\n"
        "    def stop(self):\n"
        "        return None\n"
        "\n"
        "def helper(value):\n"
        "    return value * 2\n"
    ),
    "vllm/old/place.py": "def relocated(a):\n    return a - 1\n\ndef rewritten(a):\n    return a\n",
}


def _tree(root, files):
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tool.Tree(root)


def _side(tmp_path, name, changes, removed=()):
    files = {**REFERENCE, **changes}
    for rel in removed:
        files.pop(rel)
    return _tree(tmp_path / name, files)


@pytest.fixture()
def reference(tmp_path):
    return _tree(tmp_path / "reference", REFERENCE)


def test_identical_file_is_pin_only_update(tmp_path, reference):
    side = _side(tmp_path, "same", {})
    result = tool.compare_point(reference, side, "vllm.core", "Engine.step")
    assert result["status"] == "FILE_IDENTICAL"
    assert tool.SUGGESTED[result["status"]] == "a"


def test_unrelated_edit_keeps_function_digest(tmp_path, reference):
    text = REFERENCE["vllm/core.py"] + "\nUNRELATED = 3\n"
    side = _side(tmp_path, "tail", {"vllm/core.py": text})
    assert tool.compare_point(reference, side, "vllm.core", "Engine.step")["status"] == "CODE_IDENTICAL"


def test_line_shift_is_detected_but_still_pin_only(tmp_path, reference):
    side = _side(tmp_path, "shift", {"vllm/core.py": "# new header\n\n" + REFERENCE["vllm/core.py"]})
    result = tool.compare_point(reference, side, "vllm.core", "Engine.step")
    assert result["status"] == "CODE_EQUAL_LINES_SHIFTED"
    ref = tool._function(reference, "vllm.core", "Engine.step")
    assert result["code_sha256"] != ref["exact"]  # 고정값 자체는 새로 계산해야 한다


def test_docstring_only_change_is_separated_from_logic_change(tmp_path, reference):
    docstring = REFERENCE["vllm/core.py"].replace('"""Run one step."""', '"""Run one step"""')
    side = _side(tmp_path, "doc", {"vllm/core.py": docstring})
    assert tool.compare_point(reference, side, "vllm.core", "Engine.step")["status"] == "DOCSTRING_ONLY"
    logic = REFERENCE["vllm/core.py"].replace("return x + 1", "return x + 2")
    side = _side(tmp_path, "logic", {"vllm/core.py": logic})
    result = tool.compare_point(reference, side, "vllm.core", "Engine.step")
    assert result["status"] == "CHANGED" and tool.SUGGESTED["CHANGED"] == "review"
    assert result["signature_equal"] is True
    diff = tool.point_diff(reference, side, "vllm.core", "Engine.step", result)
    assert "-        return x + 1" in diff and "+        return x + 2" in diff


def test_signature_change_is_flagged(tmp_path, reference):
    text = REFERENCE["vllm/core.py"].replace("def step(self, x):", "async def step(self, x, y=0):")
    side = _side(tmp_path, "sig", {"vllm/core.py": text})
    result = tool.compare_point(reference, side, "vllm.core", "Engine.step")
    assert result["status"] == "CHANGED" and result["signature_equal"] is False


def test_moved_function_is_found_and_compared(tmp_path, reference):
    side = _side(tmp_path, "moved", {"vllm/new/place.py": REFERENCE["vllm/old/place.py"].replace("return a\n", "return -a\n")},
                 removed=("vllm/old/place.py",))
    moved = tool.compare_point(reference, side, "vllm.old.place", "relocated")
    assert moved["status"] == "MOVED_EQUAL" and moved["moved"][0]["module"] == "vllm.new.place"
    assert tool.SUGGESTED["MOVED_EQUAL"] == "b"
    changed = tool.compare_point(reference, side, "vllm.old.place", "rewritten")
    assert changed["status"] == "MOVED_CHANGED"
    assert "+    return -a" in tool.point_diff(reference, side, "vllm.old.place", "rewritten", changed)


def test_absent_function_and_module(tmp_path, reference):
    side = _side(tmp_path, "absent", {"vllm/core.py": "class Engine:\n    def step(self, x):\n        return x\n"},
                 removed=("vllm/old/place.py",))
    assert tool.compare_point(reference, side, "vllm.core", "helper")["status"] == "ABSENT"
    assert tool.compare_point(reference, side, "vllm.old.place", "relocated")["status"] == "MODULE_ABSENT"
    assert tool.compare_point(reference, side, "vllm.core", "Engine.missing")["status"] == "REFERENCE_MISSING"


def test_compare_marks_tag_source_pins_as_candidates(tmp_path, reference):
    inv = {"modules": {"vllm.core": {"pins": {reference.file("vllm.core")["sha256"]: ["vllm_x.SOURCES"]},
                                     "points": {"Engine.step": ["vllm_x.TARGETS"], "helper": ["vllm_y.TARGETS"]}}},
           "other_pins": {}}
    same = _side(tmp_path, "same", {})
    image = tool.Tree(tmp_path / "same", image=True)
    result = tool.compare(inv, reference, {"base": same, "image": image})
    row = result["modules"][0]
    assert row["reference_matches_pin"] is True
    assert row["sides"]["base"]["pin_kind"] == "candidate_pin"
    assert row["sides"]["image"]["pin_kind"] == "image_pin"
    assert result["point_summary"]["base"]["FILE_IDENTICAL"] == 2
    assert result["by_observer"]["vllm_x"]["points"]["base"] == {"vllm.core:Engine.step": "FILE_IDENTICAL"}
    assert result["by_observer"]["vllm_y"]["modules"] == ["vllm.core"]
    json.dumps(result)


def test_table_key_and_point_parsing():
    assert tool._module_name("vllm.v1.core.sched.scheduler") == "vllm.v1.core.sched.scheduler"
    assert tool._module_name("v1/engine/async_llm.py") == "vllm.v1.engine.async_llm"
    assert tool._module_name("vllm/v1/request.py") == "vllm.v1.request"
    for rejected in ("torch.py", "__init__.py", "guard_fault_control.py", "../x.py", "/abs/x.py", "uvicorn.protocols"):
        assert tool._module_name(rejected) is None
    assert tool._points(("Engine.step", "f" * 64)) == {"Engine.step"}
    assert tool._points({"Engine": ("step", "stop")}) == {"Engine.step", "Engine.stop"}
    assert tool._points("e" * 64) == set()


def test_inventory_reads_real_observer_tables():
    inv = tool.inventory()
    scheduler = inv["modules"]["vllm.v1.core.sched.scheduler"]
    assert "f820bee4f42b878b262a30f3728a97d7413477b2a277748e20f2528ac2ec2f2a" in scheduler["pins"]
    assert "Scheduler.finish_requests" in scheduler["points"]
    cache = inv["modules"]["vllm.v1.core.kv_cache_manager"]
    assert any(o.startswith("vllm_cache_callback.") for o in cache["points"]["KVCacheManager.get_computed_blocks"])
    assert "uvicorn.protocols.http.h11_impl" in inv["other_pins"]
    assert not any(name.startswith("vllm.guard_") for name in inv["modules"])


def test_tree_requires_engine_package(tmp_path):
    with pytest.raises(ValueError, match="PIN_COMPARE_TREE_INVALID"):
        tool.Tree(tmp_path)
