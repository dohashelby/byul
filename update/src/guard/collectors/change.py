"""CHG 입력의 공통 검증과 내용 주소 근거 보존. 준비 검사는 읽기만 한다."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

import yaml

from guard.models import Change, DataOrigin, Plan, new_id, sha256_hex, utcnow
from guard.models.objects import Artifact


@dataclass(frozen=True)
class ChangeInput:
    change: Change
    payloads: dict[str, bytes]


def resolve_plan(store, value: str | Plan | None) -> Plan | None:
    if value is None:
        return None
    if isinstance(value, Plan):
        obj = store.resolve(value.ref())
    else:
        ident, sep, revision = value.partition("@")
        obj = store.get(ident, int(revision)) if sep else store.latest(ident)
        obj = store.resolve(obj.ref())
    if not isinstance(obj, Plan):
        raise ValueError("previous-plan must reference a PLAN")
    return obj


def validate_change(change: Change, pack) -> None:
    if not change.hash_ok():
        raise ValueError("CHG content hash is invalid")
    ids = [fact.id for fact in change.facts]
    if len(set(ids)) != len(ids):
        raise ValueError("CHG fact IDs must be unique")
    for fact in change.facts:
        if set(fact.related_checks) - set(pack.checks):
            raise ValueError("CHG references an unknown check ID")
        for evidence in fact.evidence:
            if not re.fullmatch(r"[0-9a-f]{64}", evidence.sha256):
                raise ValueError("CHG evidence requires a SHA-256 hash")


def _read_payload(root: Path, name: str) -> bytes:
    path = Path(name)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("CHG evidence paths must stay inside their input directory")
    target = (root / path).resolve()
    if not target.is_relative_to(root.resolve()) or not target.is_file():
        raise ValueError("CHG evidence is missing or outside its input directory")
    return target.read_bytes()


def read_change(value: str | Path | ChangeInput | Change | None, pack, store=None) -> ChangeInput | None:
    if value is None:
        return None
    if isinstance(value, ChangeInput):
        result = value
    elif isinstance(value, Change):
        result = ChangeInput(value, {})
    else:
        path = Path(value)
        if path.is_file():
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("CHG input must be a mapping")
            sealed = bool(raw.get("content_sha256"))
            raw.setdefault("id", new_id("CHG"))
            raw.setdefault("data_origin", DataOrigin.DECLARED.value)
            change = Change.model_validate(raw)
            if sealed and not change.hash_ok():
                raise ValueError("CHG content hash is invalid")
            change = change if sealed else change.seal()
            names = {e.path for fact in change.facts for e in fact.evidence}
            names.update(a.path for a in change.artifacts or [])
            payloads = {}
            for name in names:
                root = store.root if store is not None and name.startswith("artifacts/") else path.parent
                payloads[name] = _read_payload(root, name)
            result = ChangeInput(change, payloads)
        elif store is not None:
            ident, sep, revision = str(value).partition("@")
            change = store.get(ident, int(revision)) if sep else store.latest(ident)
            if not isinstance(change, Change):
                raise ValueError("--change must reference a CHG")
            change = store.resolve(change.ref())
            payloads = {a.path: _read_payload(store.root, a.path) for a in change.artifacts or []}
            result = ChangeInput(change, payloads)
        else:
            raise ValueError("CHG file is missing; stored CHG requires an existing store")
    validate_change(result.change, pack)
    if result.change.artifacts is not None:
        if any(a.path != f"artifacts/{a.sha256}" for a in result.change.artifacts):
            raise ValueError("CHG artifacts must use content addresses")
        addresses = {(a.path, a.sha256) for a in result.change.artifacts}
        if any((e.path, e.sha256) not in addresses for fact in result.change.facts for e in fact.evidence):
            raise ValueError("CHG fact evidence is not preserved by its artifacts")
    # Legacy CHG keeps its unverified evidence boundary. New inputs that provide
    # bytes are checked before any Store/output is created.
    for fact in result.change.facts:
        for evidence in fact.evidence:
            data = result.payloads.get(evidence.path)
            if data is not None and sha256_hex(data) != evidence.sha256:
                raise ValueError("CHG evidence hash mismatch")
    for artifact in result.change.artifacts or []:
        data = result.payloads.get(artifact.path)
        if data is None or sha256_hex(data) != artifact.sha256 or len(data) != artifact.bytes:
            raise ValueError("CHG artifact is missing or changed")
    return result


def extracted_input(extracted, pack) -> ChangeInput:
    change = extracted.change
    names = {e.path for fact in change.facts for e in fact.evidence}
    result = ChangeInput(change, {name: extracted.artifact_bytes for name in names})
    # Even an empty diff must retain its rule and source provenance artifact.
    if not result.payloads:
        result = ChangeInput(change, {getattr(extracted, "artifact_name", "change-evidence.json"): extracted.artifact_bytes})
    return read_change(result, pack)


def save_change(store, value: ChangeInput) -> Change:
    change = value.change
    if change.artifacts is not None:
        for artifact in change.artifacts:
            stored = store.put_artifact(value.payloads[artifact.path])
            if stored.path != artifact.path:
                raise ValueError("stored CHG artifacts must use content addresses")
        store.put(change)
        return change
    if not value.payloads:
        store.put(change)
        return change
    mapped = {}
    artifacts = []
    for name, data in sorted(value.payloads.items()):
        stored = store.put_artifact(data)
        mapped[name] = stored.path
        if any(a.path == stored.path for a in artifacts):
            continue
        artifacts.append(Artifact(name=Path(name).name, path=stored.path, sha256=stored.sha256,
                                  bytes=stored.bytes, collected_at=utcnow(), collector=change.extractor.name))
    facts = [fact.model_copy(update={"evidence": [e.model_copy(update={"path": mapped.get(e.path, e.path)})
                                                  for e in fact.evidence]}) for fact in change.facts]
    saved = change.model_copy(update={"facts": facts, "artifacts": artifacts}).seal()
    store.put(saved)
    return saved
