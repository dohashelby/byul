"""기존 guard manifest/inventory를 그대로 쓰는 로컬 snapshot 사전 대조."""
from pathlib import Path

from .model_manifest import _load_manifest, build_manifest


def inspect_snapshot(snapshot, reference, *, expected_root=None):
    """새 inventory 형식·해시·다운로드·판정을 만들지 않는다.

    reference는 guard.model-manifest/1, scope=all이다. MATCH도 실제 worker의
    읽기/적재나 공급물 승인을 증명하지 않는다. 부분 inventory에 완전 hash를 붙이지 않는다.
    """
    try:
        if (type(reference) is not dict or reference.get("schema") != "guard.model-manifest/1"
                or type(reference.get("files")) is not list or not reference["files"]
                or any(type(row) is not dict or type(row.get("bytes")) is not int for row in reference["files"])):
            raise ValueError
        expected = _load_manifest(reference, None)
        if (type(expected.get("file_count")) is not int or type(expected.get("total_bytes")) is not int
                or expected.get("file_count") != len(expected["files"])
                or expected.get("total_bytes") != sum(row["bytes"] for row in expected["files"])):
            raise ValueError
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ValueError("MODEL_SNAPSHOT_REFERENCE_INVALID") from None
    result = {"status": "UNAVAILABLE", "reference_inventory_sha256": expected["inventory_sha256"],
        "observed_manifest": None, "missing": [], "changed": [], "unexpected": [],
        "diagnostic_codes": [], "worker_consumption_verified": False}
    root = Path(snapshot)
    try:
        if expected_root is not None and root.resolve() != Path(expected_root).resolve():
            result["diagnostic_codes"] = ["MODEL_SNAPSHOT_ROOT_MISMATCH"]
            return result
        if not root.is_dir():
            raise OSError
        actual = build_manifest(root, model_id=expected.get("model_id"))
    except (OSError, ValueError, RuntimeError):
        result["diagnostic_codes"] = ["MODEL_SNAPSHOT_UNAVAILABLE"]
        return result
    before = {row["path"]: row for row in expected["files"]}
    after = {row["path"]: row for row in actual["files"]}
    result.update(observed_manifest=actual, missing=sorted(before.keys() - after.keys()),
        unexpected=sorted(after.keys() - before.keys()),
        changed=sorted(p for p in before.keys() & after.keys() if before[p] != after[p]))
    result["status"] = "MATCH" if actual["inventory_sha256"] == expected["inventory_sha256"] else "MISMATCH"
    if result["status"] == "MISMATCH":
        result["diagnostic_codes"] = ["MODEL_SNAPSHOT_INVENTORY_MISMATCH"]
    return result
