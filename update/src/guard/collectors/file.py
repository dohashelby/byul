"""파일 수집기: env.yaml → ENV, policy.yaml → POL (사람이 신고한 값).

사실의 축약형 `gateway: true`는 {value, origin, source}로 펼친다. origin은 파일의 data_origin이
EXAMPLE이면 EXAMPLE, 아니면 DECLARED다. 파일 수집기는 축약형으로 MEASURED를 만들지 않는다(INV-02).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from guard.models import DataOrigin, Environment, Pack, Policy, new_id


def load_bounded_json(path: str | Path) -> Any:
    """Read a local JSON document of at most 64 KiB."""
    from guard.executors.owned_capture import strict_json

    limit = 64 * 1024
    with Path(path).open("rb") as stream:
        return strict_json(stream.read(limit + 1), limit=limit)


def _read(path: Path, *, raw: bytes | None = None) -> dict[str, Any]:
    text = (Path(path).read_text(encoding="utf-8") if raw is None
            else raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n"))
    data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top level must be a mapping")
    return data


def load_environment(path: Path, *, raw: bytes | None = None) -> Environment:
    """Load the environment file or parse its already-verified bytes."""
    data = _read(path, raw=raw)
    if data.get("kind") == "ENV":
        obj = Environment.model_validate(data)
        if not obj.hash_ok():
            raise ValueError("ENV content hash is invalid")
        return obj
    origin = DataOrigin(data.get("data_origin", "DECLARED"))
    shorthand_origin = DataOrigin.EXAMPLE if origin == DataOrigin.EXAMPLE else DataOrigin.DECLARED
    facts: dict[str, dict] = {}
    for key, fact in (data.get("facts") or {}).items():
        if isinstance(fact, dict):
            fv = dict(fact)
            fv.setdefault("source", f"{Path(path).name}#facts.{key}")
            if fv.get("value") is not None:
                fv.setdefault("origin", shorthand_origin)
        else:
            fv = {"value": fact}
            if fact is not None:
                fv.update(origin=shorthand_origin, source=f"{Path(path).name}#facts.{key}")
        facts[key] = fv
    return Environment(
        id=new_id("ENV"),
        data_origin=origin,
        engine=data["engine"],
        adapter=data.get("adapter"),
        runtime=data.get("runtime") or {},
        model=data.get("model") or {},
        facts=facts,
    ).seal()


def load_policy(path: Path, pack: Pack, *, raw: bytes | None = None) -> Policy:
    """Load the policy file or parse its already-verified bytes."""
    data = _read(path, raw=raw)
    if data.get("kind") == "POL":
        obj = Policy.model_validate(data)
        if not obj.hash_ok() or obj.pack != pack.ref():
            raise ValueError("POL hash or pack reference is invalid")
        return obj
    want = data.get("pack") or {}
    ref = pack.ref()
    if (want.get("id") != ref.id or int(want.get("revision", -1)) != ref.revision
            or "sha256" in want and want["sha256"] != ref.sha256):
        raise ValueError(
            f"{path}: policy is for {want.get('id')} rev {want.get('revision')}, "
            f"loaded pack is {ref.id} rev {ref.revision}"
        )
    return Policy(
        id=new_id("POL"),
        data_origin=DataOrigin(data.get("data_origin", "DECLARED")),
        status=data.get("status", "DRAFT"),
        pack=ref,
        values=data.get("values") or {},
        scope=data.get("scope") or {},
        exceptions=data.get("exceptions") or [],
        confirmed_by=data.get("confirmed_by"),
        confirmed_at=data.get("confirmed_at"),
        valid_from=data.get("valid_from"),
        valid_until=data.get("valid_until"),
        source_document_sha256=data.get("source_document_sha256"),
    ).seal()
