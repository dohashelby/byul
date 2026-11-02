"""소스 책임에서 작성한 부분 정답으로 경로 분류의 경계와 누락을 검증한다.

이 자료는 이미 규칙 설계에 사용한 v0.32.0의 개발·회귀 사례다. 테스트
통과가 독립 평가나 실행·보안 판정의 성공을 뜻하지 않는다.
"""

import json
from pathlib import Path
import re

import pytest
import yaml

from guard.adapters.git_diff import RULES_PATH, classify_path
from guard.models.checkdef import load_pack


ROOT = Path(__file__).resolve().parents[1]
BASIS_PATH = ROOT / "tests/fixtures/u1_path_rule_basis.json"
BASIS = json.loads(BASIS_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def path_rules():
    return yaml.safe_load(RULES_PATH.read_bytes())


@pytest.mark.parametrize("case", BASIS["examples"], ids=lambda case: case["id"])
def test_reviewed_path_responsibility_and_counterexamples(path_rules, case):
    categories, excluded = classify_path(case["path"], path_rules)
    assert categories == case["categories"]
    assert excluded is case["excluded"]


def test_rule_basis_is_partial_and_not_an_independent_or_human_approved_oracle(path_rules):
    assert BASIS["dataset_role"] == "DEVELOPMENT_REGRESSION"
    assert BASIS["independent_evaluation"] is False
    assert BASIS["human_review_status"] == "PENDING"
    assert BASIS["limitations"]
    groups = {group["id"]: group for group in BASIS["groups"]}
    referenced = {rule["basis"] for rule in path_rules["rules"] if "basis" in rule}
    assert referenced == groups.keys()
    for group in groups.values():
        assert group["scope"] and group["reason"] and group["counterexample"]
        assert group["limitation"]
        assert group["review_status"] == "AGENT_REVIEWED_HUMAN_PENDING"
        assert group["source"]
        for source in group["source"]:
            assert source["path"] and source["anchor"]
            assert re.fullmatch("[0-9a-f]{64}", source["sha256"])
    assert re.fullmatch("[0-9a-f]{40}", BASIS["source_commit"])
    assert all(case["basis"] == "AGENT_AUTHORED_PARTIAL_ORACLE" for case in BASIS["examples"])


def test_rules_only_use_existing_categories_and_keep_output_parser_gap(path_rules):
    pack = load_pack(ROOT / "reference/pack-seeds/PACK-BASELINE-31")
    existing = set(pack.change_selection["engine_categories"])
    assert {category for rule in path_rules["rules"] for category in rule["categories"]} <= existing
    assert pack.change_selection["engine_categories"]["OUTPUT_PARSING"] == []
    assert "OUTPUT_PARSING" in classify_path("vllm/parser/abstract_parser.py", path_rules)[0]
    assert "OUTPUT_PARSING" in classify_path("rust/src/parser/src/lib.rs", path_rules)[0]


def test_broad_names_do_not_hide_unknown_or_claim_unrelated_responsibility(path_rules):
    # 임의 이름만으로 미검토 변경을 인식한 영역으로 숨기지 않는다.
    for name in ("author", "cache", "stream"):
        assert classify_path(f"vllm/new_domain/{name}.py", path_rules) == ([], False)
    for parent in ("config", "utils", "platforms", "engine", "v1/engine"):
        assert classify_path(f"vllm/{parent}/unreviewed.py", path_rules) == ([], False)
    assert not any(rule.get("contains") in {"middleware", "auth", "cache", "stream"}
                   for rule in path_rules["rules"])
    assert not any(rule.get("categories") == ["GENERIC"] for rule in path_rules["rules"])


def test_no_new_exclusions_are_used_to_reduce_unmapped(path_rules):
    assert path_rules["exclude"] == {
        "prefixes": ["tests/", "docs/", ".buildkite/", "benchmarks/", "examples/", ".github/",
                     "tools/", "vllm/benchmarks/", "rust/src/bench/"],
        "components": ["tests", "benches", "examples"],
        "suffixes": [".md", ".rst"],
    }
    # 미검토 제품 경로와 build·운영 자료는 제외 대신 보수적 미분류로 남긴다.
    for path in ("vllm/snapshot/runtime.py", "vllm/v1/utils.py", ".gitignore", "mkdocs.yaml"):
        assert classify_path(path, path_rules) == ([], False)


def test_original_69_review_covers_every_record_and_source_side_without_claiming_human_approval():
    review = BASIS["original69_review"]
    records = review["records"]
    assert len(records) == review["counts"]["records"] == 69
    assert len({row["after_path"] or row["before_path"] for row in records}) == 69
    assert sum(len(row["source_views"]) for row in records) == 124
    assert len({row["id"] for row in records}) == 69
    assert review["baseline"]["commit"] == "db9527a46873454610df6dbedf79a36d6bf1a7f6"
    assert review["candidate"]["commit"] == BASIS["source_commit"]
    assert re.fullmatch("[0-9a-f]{64}", review["source_artifact_sha256"])
    assert re.fullmatch("[0-9a-f]{64}", review["previous_rules_sha256"])
    for row in records:
        assert row["responsibility"] and row["change_summary"] and row["reason"]
        assert row["counterexample_or_risk"] and row["selection_effect"]
        assert row["human_question"]
        assert row["review_status"] == "AGENT_REVIEWED_HUMAN_PENDING"
        assert row["disposition"] in {"mapped", "refined", "unmapped", "exclusion_proposed"}
        for view in row["source_views"]:
            assert view["side"] in {"baseline", "candidate"}
            assert re.fullmatch("[0-9a-f]{64}", view["sha256"])
            assert view["path"] == row["before_path" if view["side"] == "baseline" else "after_path"]


@pytest.mark.parametrize("case", BASIS["original69_review"]["records"], ids=lambda case: case["id"])
def test_each_original_unmapped_path_retains_its_source_based_review_contract(path_rules, case):
    path = case["after_path"] or case["before_path"]
    assert classify_path(path, path_rules) == (case["categories"], False)


def test_reviewed_exact_files_do_not_expand_to_unknown_config_utils_or_engine_files(path_rules):
    review = BASIS["original69_review"]
    rules = [rule for rule in path_rules["rules"] if rule.get("basis") == "review69-exact-files"]
    mapped = [row for row in review["records"] if row["disposition"] == "mapped"]
    assert len(rules) == len(mapped) == 37
    assert all(set(rule) == {"path", "categories", "basis"} for rule in rules)
    assert {rule["path"] for rule in rules} == {
        row["after_path"] or row["before_path"] for row in mapped
    }
    for path in ("vllm/config/attention_extra.py", "vllm/utils/network_utils_extra.py",
                 "vllm/v1/engine/core_client_extra.py", "vllm/config/vllm.py",
                 "vllm/utils/torch_utils.py", "vllm/v1/engine/core.py"):
        assert classify_path(path, path_rules) == ([], False)


def test_exclusion_proposals_remain_unmapped_until_a_team_decision(path_rules):
    review = BASIS["original69_review"]
    proposals = [row for row in review["records"] if row["disposition"] == "exclusion_proposed"]
    assert len(proposals) == 17
    for row in proposals:
        assert row["categories"] == []
        assert row["rule"] is None
        assert classify_path(row["after_path"] or row["before_path"], path_rules) == ([], False)


def test_output_grammar_and_kernel_templates_are_not_input_chat_templates(path_rules):
    for path in ("vllm/parser/response_template.py", "vllm/parser/chat_parsing/response_templates.py",
                 "rust/src/parser/src/unified/hf/template.rs"):
        categories, excluded = classify_path(path, path_rules)
        assert categories == ["OUTPUT_PARSING"]
        assert excluded is False
    assert classify_path("csrc/quantization/marlin/marlin_template.h", path_rules) == (["RUNNER_KERNEL"], False)
    assert classify_path("vllm/transformers_utils/chat_templates/registry.py", path_rules) == (
        ["INPUT_PROCESSING", "MODEL_LOADING", "TEMPLATE"], False)
    assert classify_path("vllm/new_domain/template.py", path_rules) == ([], False)
    assert not any(rule.get("contains") == "template" for rule in path_rules["rules"])


def test_other_name_rules_are_only_partially_reviewed_and_not_a_complete_accuracy_claim(path_rules):
    review = BASIS["limited_name_review"]
    assert review["status"] == "AGENT_REVIEWED_HUMAN_PENDING"
    assert review["template"]["action"] == "REFINED"
    for name in ("sampling", "lora", "metrics"):
        assert review[name]["action"] == "PRESERVED_PARTIAL_REVIEW"
        assert review[name]["limitation"]
        assert re.fullmatch("[0-9a-f]{64}", review[name]["source"]["sha256"])
        assert any(rule.get("contains") == name for rule in path_rules["rules"])
