import json

from guard.adapters import vllm_release_observation_sensor as release
from tools import ci_preflight, verify_req10_consumption as source_review


def test_preflight_reports_pin_profile_and_receipt_errors_in_one_run(tmp_path, monkeypatch, capsys):
    def write(path, data):
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data), encoding="utf-8")

    source = ci_preflight.ADAPTERS / "vllm_lifecycle_sensor.py"
    write(source, "changed source")
    write(source_review.MANIFEST_PATH, {
        "schema": source_review.MANIFEST_SCHEMA,
        "paths": list(source_review.PINNED_PATHS),
        "files": {source.as_posix(): "a" * 64, "update/src/guard/missing.py": "b" * 64},
    })
    write(ci_preflight.RECEIPTS, {"schema": source_review.RECEIPTS_SCHEMA, "receipts": []})
    write(ci_preflight.ABI_CATALOG, {
        "schema": "guard-observer-abi/1",
        "base": {"file": source.name, "sha256": "c" * 64},
        "observers": {"another": {"file": "another.py", "sha256": "d" * 64}},
    })
    write(ci_preflight.ENGINE_CATALOG, {"profiles": [{"id": "same"}, {"id": "same"}]})
    profile, schema = next(iter(release.PROFILES.values()))
    write(ci_preflight.LIFECYCLE_CATALOG, {"profiles": [{
        "profile": profile,
        "schema": schema,
        "sources": {
            "first.module": {"classes": {"Worker": ["run"]}},
            "second.module": {"classes": {"Worker": ["run"]}},
        },
    }]})
    files_before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}

    monkeypatch.setattr(ci_preflight, "REPO", tmp_path)
    assert ci_preflight.main() == 1

    report = capsys.readouterr().out
    assert f"{source.as_posix()} ({source_review.MANIFEST_PATH}): expected {'a' * 64}" in report
    assert "update/src/guard/missing.py: missing from checkout" in report
    assert f"{source.as_posix()} ({ci_preflight.ABI_CATALOG}, base): expected {'c' * 64}" in report
    assert "another.py" in report and f"{'d' * 64}, found missing" in report
    assert "has 0 matching review records" in report
    assert "duplicate id 'same'" in report
    assert f"duplicate profile {profile!r}" in report
    assert f"duplicate schema {schema!r}" in report
    assert "duplicate point 'Worker.run': first.module, second.module" in report
    assert {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == files_before
