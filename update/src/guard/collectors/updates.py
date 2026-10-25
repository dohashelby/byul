"""사람의 명시적 부분 갱신을 같은 객체의 새 revision으로 보존한다."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import yaml

from guard.models import DataOrigin, Environment, Policy, utcnow


def _merge(base, patch):
    out = deepcopy(base)
    for key, value in patch.items():
        out[key] = _merge(out[key], value) if isinstance(value, dict) and isinstance(out.get(key), dict) else deepcopy(value)
    return out


def update_object(store, base: str, patch_path: Path, *, kind: str):
    ident, _, _ = base.partition("@")
    prior = store.latest(ident)
    cls = {"ENV": Environment, "POL": Policy}[kind]
    if not isinstance(prior, cls):
        raise ValueError("base object kind differs from update command")
    patch = yaml.safe_load(Path(patch_path).read_text(encoding="utf-8"))
    if not isinstance(patch, dict):
        raise ValueError("patch must be a mapping")
    allowed = {"engine", "adapter", "runtime", "model", "facts"} if kind == "ENV" else {
        "values", "scope", "exceptions", "status", "confirmed_by", "confirmed_at"}
    if set(patch) - allowed:
        raise ValueError("patch contains immutable or unsupported fields")
    patch = deepcopy(patch)
    if kind == "ENV":
        facts = patch.get("facts", {})
        if not isinstance(facts, dict):
            raise ValueError("facts patch must be a mapping")
        for key, value in list(facts.items()):
            value = value if isinstance(value, dict) else {"value": value}
            if value.get("origin") == "MEASURED":
                raise ValueError("a manual patch cannot introduce MEASURED facts")
            # Replace the complete changed fact so old source/absence/conflict
            # metadata cannot silently substantiate a newly declared value.
            facts[key] = {"value": value.get("value"), "origin": value.get("origin", "DECLARED"),
                          "source": value.get("source", f"{patch_path.name}#facts.{key}"),
                          "absence_evidence": value.get("absence_evidence"), "conflict": value.get("conflict")}
        facts_patch = patch.pop("facts", None)
    else:
        facts_patch = None
    data = _merge(prior.to_dict(), patch)
    if facts_patch is not None:
        data["facts"].update(facts_patch)
    data.update(revision=prior.revision + 1, created_at=utcnow(), content_sha256="",
                data_origin=DataOrigin.EXAMPLE if prior.data_origin == DataOrigin.EXAMPLE else DataOrigin.DECLARED,
                refs=[*prior.refs, prior.ref()])
    obj = cls.model_validate(data).seal()
    store.put(obj)
    return obj
