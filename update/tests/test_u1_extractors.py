"""U1 추출 계약: 오프라인 원본 바이트·분류·근거·모델 파일 변경."""

import copy
import hashlib
import json
from pathlib import Path
import subprocess

import pytest
import yaml

from guard.adapters.git_diff import RULES_PATH, classify_path, extract_git_change
from guard.collectors.model_manifest import CATEGORIES, build_manifest, extract_model_change
from guard.models.checkdef import CheckDef, PackIntegrityError, build_pack, load_pack
from guard.models.hashing import canonical_json, sha256_hex
from guard.runtime.git import GitError, resolve_commit
from guard.runtime import git as source_git
from tools.import_checklist import AREAS, import_checklist

ROOT = Path(__file__).resolve().parents[1]


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True).stdout


@pytest.fixture
def source_repo(tmp_path):
    repo = tmp_path / "source"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "offline@example.invalid")
    _git(repo, "config", "user.name", "Offline fixture")
    _git(repo, "config", "core.autocrlf", "false")
    paths = {"vllm/entrypoints/old.py": b"@router.get('/old')\r\ndef old(): pass\r\n",
             "vllm/inputs/deleted.py": b"removed\r\n", "docs/old.md": b"old docs\r\n"}
    for name, raw in paths.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "baseline")
    _git(repo, "tag", "v1.0.0")
    (repo / "vllm/entrypoints/old.py").rename(repo / "vllm/entrypoints/new.py")
    (repo / "vllm/inputs/deleted.py").unlink()
    for name, raw in {"vllm/entrypoints/admin.py": b"@router.post('/newadmin')\ndef admin(): pass\n",
                      "vllm/new_domain/strange.py": b"raise RuntimeError('must never import')\n",
                      "vllm/tool_parsers/new.py": b"parser = 1\n", "CMakeLists.txt": b"project(fixture)\n",
                      "tests/test_routes.py": b"untrusted = 1\n"}.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "candidate")
    _git(repo, "tag", "v1.0.1")
    return repo


def test_git_source_bytes_rename_delete_exclusions_and_artifact(source_repo):
    extraction = extract_git_change(source_repo, "v1.0.0", "v1.0.1", pack=load_pack(ROOT / "packs/PACK-BASELINE"))
    change = extraction.change
    artifact = json.loads(extraction.artifact_bytes)
    assert len(change.baseline.source_commit) == 40
    assert change.extractor.name == "git_diff" and change.hash_ok()
    renamed = next(row for row in artifact["changes"] if row["status"].startswith("R"))
    assert renamed["before_path"] == "vllm/entrypoints/old.py"
    assert renamed["after_path"] == "vllm/entrypoints/new.py"
    raw = b"@router.get('/old')\r\ndef old(): pass\r\n"
    assert renamed["before_sha256"] == renamed["after_sha256"] == hashlib.sha256(raw).hexdigest()
    assert any(row["status"] == "D" and row["after_sha256"] is None for row in artifact["changes"])
    assert any(row["after_path"] == "tests/test_routes.py" for row in artifact["excluded"])
    assert not any(f.target.startswith("tests/") for f in change.facts)
    assert all(f.evidence[0].sha256 == sha256_hex(extraction.artifact_bytes) for f in change.facts)
    assert "CMakeLists.txt" in change.analysis["category_files"]["IMAGE"]
    assert change.analysis["unmapped_files"] == ["vllm/new_domain/strange.py"]
    assert change.analysis["checklist_gap_candidates"] == ["vllm/tool_parsers/new.py"]
    assert all(f.certainty != "CONFIRMED" for f in change.facts)
    assert change.analysis["source_image_relation"] == "UNVERIFIED"
    assert all(f.category != "ADVISORY" for f in change.facts)


def test_git_change_reproducible_artifact_without_personal_path(source_repo):
    first = extract_git_change(source_repo, "v1.0.0", "v1.0.1")
    second = extract_git_change(source_repo, "v1.0.0", "v1.0.1")
    assert first.artifact_bytes == second.artifact_bytes
    assert first.change.facts == second.change.facts
    assert str(source_repo).encode() not in first.artifact_bytes
    assert b"created_at" not in first.artifact_bytes


def test_source_entrypoint_is_registration_not_handler_reach(source_repo):
    change = extract_git_change(source_repo, "v1.0.0", "v1.0.1").change
    fact = next(f for f in change.facts if f.target.endswith("admin.py"))
    assert fact.details["entrypoint"] is True
    assert fact.details["entrypoint_kind"] == "http"
    assert fact.details["routes"][0]["path"] == "/newadmin"
    assert fact.details["routes"][0]["registration_only"] is True
    assert "used" not in fact.details and "handler_reached" not in fact.details


@pytest.mark.parametrize("path,categories,ignored", [
    ("rust/src/server/src/routes.rs", ["API_ROUTE"], False),
    ("vllm/entrypoints/grpc_server.py", ["API_ROUTE"], False),
    ("vllm/model_executor/models/example.py", ["MODEL_LOADING", "RUNNER_KERNEL"], False),
    ("CMakeLists.txt", ["IMAGE"], False),
    ("vllm/a/tests/a.py", [], True),
    ("rust/src/chat/examples/test.rs", [], True),
    ("unknown/new.format", [], False),
])
def test_path_rules(path, categories, ignored):
    rules = yaml.safe_load(RULES_PATH.read_bytes())
    actual, was_ignored = classify_path(path, rules)
    assert actual == categories and was_ignored == ignored


def test_git_rejects_option_revision(source_repo):
    with pytest.raises(GitError, match="invalid_revision"):
        resolve_commit(source_repo, "--help")


def test_promisor_blob_fetch_uses_noop_negotiation_and_stdin_batches(monkeypatch, tmp_path):
    oids = [f"{index:040x}" for index in range(1, 202)]
    requests = [("a" * 40, f"source/{index}.py") for index in range(len(oids))]
    fetched = []

    def fake_git(repo, *args, input_bytes=None, no_lazy_fetch=False):
        if args[0] == "config":
            return b"true\n"
        if args[0] == "ls-tree":
            return b"".join(f"100644 blob {oid}\tsource/{index}.py\0".encode()
                            for index, oid in enumerate(oids))
        if args[0] == "cat-file":
            assert no_lazy_fetch is True
            return b"".join(oid.encode() + b" missing\n" for oid in oids)
        assert args == ("-c", "fetch.negotiationAlgorithm=noop", "fetch", "--no-tags",
                        "--no-write-fetch-head", "--recurse-submodules=no", "--filter=blob:none", "--stdin", "origin")
        fetched.append(input_bytes.decode("ascii").splitlines())
        return b""

    monkeypatch.setattr(source_git, "run_git", fake_git)
    source_git._prefetch_missing_blobs(tmp_path, requests)
    assert list(map(len, fetched)) == [200, 1]
    assert [oid for batch in fetched for oid in batch] == oids


def test_historical_pack_fixture_is_independent_of_current_u1_files(tmp_path):
    from tests.response_semantics_support import materialize_pack21
    historical = materialize_pack21(tmp_path / "pack21")
    assert historical.manifest.revision == 21
    assert historical.change_selection is None
    assert not (historical.path / "change_selection.yaml").exists()
    assert historical.checks["REQ-09"].revision == 5 and historical.checks["PLT-04"].revision == 2
    assert historical.checks["REQ-01"].framework_id is None
    assert len(historical.checks) == 77


def _snapshots(tmp_path):
    left, right = tmp_path / "left", tmp_path / "right"
    left.mkdir()
    right.mkdir()
    config = {"architectures": ["TextModel"], "model_type": "text", "vocab_size": 10,
              "max_position_embeddings": 32}
    for path in (left, right):
        (path / "config.json").write_text(json.dumps(config), encoding="utf-8")
        (path / "tokenizer_config.json").write_text(json.dumps({"chat_template": "old"}), encoding="utf-8")
    return left, right


@pytest.mark.parametrize("name,content,expected", [
    ("model.safetensors", b"weights", {"MODEL_WEIGHTS"}),
    ("module.py", b"raise RuntimeError('never import')", {"MODEL_CODE"}),
    ("chat_template.jinja", b"{{ messages }}", {"MODEL_TEMPLATE"}),
    ("vocab.txt", b"vocabulary", {"MODEL_TOKENIZER"}),
    ("unknown.asset", b"unknown", set(CATEGORIES)),
])
def test_model_file_categories(tmp_path, name, content, expected):
    left, right = _snapshots(tmp_path)
    (right / name).write_bytes(content)
    result = extract_model_change(left, right)
    assert {f.category for f in result.change.facts} == expected
    assert all(f.certainty == "PARTIAL" for f in result.change.facts)
    assert all(f.evidence[0].sha256 == sha256_hex(result.artifact_bytes) for f in result.change.facts)


@pytest.mark.parametrize("name,key,value,expected", [
    ("config.json", "auto_map", {"AutoModel": "module.Model"}, "MODEL_CODE"),
    ("processor_config.json", "auto_map", {"AutoProcessor": "module.Processor"}, "MODEL_CODE"),
    ("tokenizer_config.json", "chat_template", "new", "MODEL_TEMPLATE"),
    ("config.json", "architectures", ["AudioModel"], "MODEL_ARCH"),
    ("config.json", "model_type", "audio", "MODEL_ARCH"),
    ("config.json", "vocab_size", 11, "MODEL_TOKENIZER"),
    ("config.json", "max_position_embeddings", 64, "MODEL_TOKENIZER"),
])
def test_model_config_key_categories(tmp_path, name, key, value, expected):
    left, right = _snapshots(tmp_path)
    if not (left / name).exists():
        (left / name).write_text("{}", encoding="utf-8")
        (right / name).write_text("{}", encoding="utf-8")
    config = json.loads((right / name).read_text())
    config[key] = value
    (right / name).write_text(json.dumps(config), encoding="utf-8")
    assert {f.category for f in extract_model_change(left, right).change.facts} == {expected}


def test_model_id_and_manifest_integrity(tmp_path):
    left, right = _snapshots(tmp_path)
    before, after = build_manifest(left, model_id="model-a"), build_manifest(right, model_id="model-b")
    assert {f.category for f in extract_model_change(before, after).change.facts} == set(CATEGORIES)
    before["files"][0]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="inventory_hash"):
        extract_model_change(before, after)


def test_past_inventory_without_settings_does_not_infer_narrow_change(tmp_path):
    left, right = _snapshots(tmp_path)
    before, after = build_manifest(left), build_manifest(right)
    before.pop("settings")
    after["files"][0]["sha256"] = "0" * 64
    after["inventory_sha256"] = sha256_hex(canonical_json(after["files"]))
    assert {f.category for f in extract_model_change(before, after).change.facts} == set(CATEGORIES)


def test_checklist_framework_metadata_and_exact_internal_mapping(tmp_path):
    source = json.loads((ROOT / "reference/framework/data/checklist.json").read_bytes())
    pack_dir = tmp_path / "pack"
    # 기존 ID 맵을 써서 질문이 모두 바뀐 원본의 73+4 연결을 검증한다.
    pack_dir.mkdir()
    (pack_dir / "id_map.yaml").write_bytes((ROOT / "packs/PACK-BASELINE/id_map.yaml").read_bytes())
    manifest = import_checklist(ROOT / "reference/framework/data/checklist.json", pack_dir, ROOT / "tools/overrides")
    pack = load_pack(pack_dir)
    assert len(manifest["checks"]) == 77
    assert len({c.framework_id for c in pack.checks.values()}) == 77
    for key, area in AREAS.items():
        for number, row in enumerate(source[key], 1):
            check = pack.checks[f"{area}-{number:02d}"]
            assert check.framework_id == row["id"]
            assert check.mechanism_ids == row["mechanism_ids"]
            assert check.legacy.subtest_ids == row["subtest_ids"]
            assert check.applies_when.prose == row["applies_when"]
    selection = pack.change_selection
    assert len(selection["patch_always"]) == 20
    assert len({c for values in selection["engine_categories"].values() for c in values}) == 56
    assert "runtime.driver" not in selection["fact_triggers"]
    for fact, refs in selection["fact_triggers"].items():
        assert refs and selection["fact_trigger_sources"][fact]


def test_legacy_checkdef_omits_new_optional_metadata():
    check = load_pack(ROOT / "packs/PACK-BASELINE").checks["REQ-01"].model_dump(mode="json")
    check.pop("framework_id")
    check.pop("mechanism_ids")
    assert CheckDef.model_validate(check).model_dump(mode="json") == check


def test_source_refresh_and_image_profile_revisions_preserve_other_definitions():
    """팩23→28의 독립 변경 단계를 구분해 각 단계의 나머지76개 bytes를 보호한다."""
    prior = {f"{area}-{number:02d}": 1 for area, count in
             (("SUP", 16), ("REQ", 21), ("PLT", 12), ("LIF", 16), ("OBS", 12))
             for number in range(1, count + 1)}
    prior.update({"SUP-01": 2, "SUP-02": 2, "SUP-14": 2, "REQ-01": 5, "REQ-03": 2,
                  "REQ-07": 2, "REQ-09": 6, "REQ-10": 2, "REQ-11": 3, "REQ-13": 2,
                  "REQ-15": 2, "REQ-16": 2, "PLT-01": 4, "PLT-04": 3, "LIF-07": 2,
                  "OBS-03": 2, "OBS-04": 2, "OBS-05": 2, "OBS-07": 2, "OBS-08": 2,
                  "OBS-09": 2, "OBS-11": 2})
    pack = load_pack(ROOT / "packs/PACK-BASELINE")
    assert pack.manifest.revision == 28
    assert set(pack.checks) == set(prior) and len(prior) == 77
    expected = {cid: revision + 1 for cid, revision in prior.items()}
    expected["SUP-04"] += 1  # Only the new running-image profile changes after source refresh.
    expected["PLT-02"] += 1  # Bounded Unix IPC access profile; all previous definitions remain unchanged.
    expected["SUP-03"] += 1  # Observation-only template profile, not independent consumer proof.
    expected["PLT-01"] += 1  # Complete permission collection gates PASS, preserving violation rules.
    expected["PLT-07"] += 1  # Only the explicit forbidden egress profile changes after PACK27.
    assert {cid: check.revision for cid, check in pack.checks.items()} == expected
    frozen = json.loads((ROOT / "tests/fixtures/pack23-check-hashes.json").read_text(encoding="utf-8"))
    previous = json.loads((ROOT / "tests/fixtures/pack24-check-hashes.json").read_text(encoding="utf-8"))
    assert frozen["pack_revision"] == 23 and len(frozen["checks"]) == 77
    assert previous["pack_revision"] == 24 and len(previous["checks"]) == 77
    from hashlib import sha256
    for path, digest in frozen["checks"].items():
        if path != "checks/SUP-04.yaml":
            assert previous["checks"][path] == digest
    # Keep the old PACK24 -> PACK25 assertion against its immutable stage snapshot.
    pack25 = json.loads((ROOT / "tests/fixtures/pack25-check-hashes.json").read_text(encoding="utf-8"))
    assert len(pack25) == 77
    for path, digest in previous["checks"].items():
        if path != "checks/SUP-03.yaml":
            assert pack25[Path(path).stem] == digest
    # PACK26 is fixed from c9c6ab4 git objects, before the permission completeness change.
    pack26 = json.loads((ROOT / "tests/fixtures/pack26-check-hashes.json").read_text(encoding="utf-8"))
    assert len(pack26) == 77
    # Keep PACK25 -> PACK26 against immutable snapshots: only the IPC definition changed.
    for cid, digest in pack25.items():
        if cid != "PLT-02":
            assert pack26[cid] == digest
    # PACK26 -> PACK27 changed only PLT-01; preserve that immutable stage too.
    pack27 = json.loads((ROOT / "tests/fixtures/pack27-check-hashes.json").read_text(encoding="utf-8"))
    assert len(pack27) == 77
    for cid, digest in pack26.items():
        if cid != "PLT-01":
            assert pack27[cid] == digest
    # PACK27 -> current changes only PLT-07; old snapshots receive no new contract.
    current = {cid: sha256((ROOT / "packs/PACK-BASELINE/checks" / f"{cid}.yaml").read_bytes()).hexdigest()
               for cid in pack27}
    assert {cid for cid, digest in pack27.items() if current[cid] != digest} == {"PLT-07"}
    assert pack.checks["SUP-04"].collection_profile == "running-image-approval-v1"
    assert {check.framework_id for check in pack.checks.values()} == {
        "CHK-" + cid.split("-")[0] + "-" + cid.split("-")[1].zfill(3) for cid in prior}


def test_pack_rejects_unknown_change_selection_reference():
    pack_dir = ROOT / "packs/PACK-BASELINE"
    manifest = yaml.safe_load((pack_dir / "pack.yaml").read_bytes())
    files = {row["path"]: (pack_dir / row["path"]).read_bytes() for row in manifest["files_sha256"]}
    selection = yaml.safe_load(files["change_selection.yaml"])
    selection["patch_always"].append("REQ-99")
    files["change_selection.yaml"] = yaml.safe_dump(selection).encode()
    for entry in manifest["files_sha256"]:
        entry["sha256"] = sha256_hex(files[entry["path"]])
    with pytest.raises(PackIntegrityError, match="unknown check"):
        build_pack(manifest, files)
