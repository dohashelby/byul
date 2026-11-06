"""Read host-produced Tetragon state without granting kubectl privileges to Gateway."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any
from serving_runtime.enforcement.verification import reassess_record

SCHEMA = "runtime-tetragon-state/1.0"


def read_tetragon_state(path: str | Path, *, context: str | None = None,
                        now: datetime | None = None, stale_after_s: float = 20) -> dict[str, Any]:
    result = {"schema_version": SCHEMA, "generated_at": None, "context": context,
              "state": "not_configured", "fresh": False, "error": None,
              "policies": [], "capabilities": [], "transitions": [], "verifications": []}
    result["agents"] = []
    try:
        target=Path(path)
        if not target.exists():
            return result
        if target.stat().st_size > 2*1024*1024:
            raise ValueError("Tetragon state exceeds size limit")
        value=json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(value,dict) or value.get("schema_version")!=SCHEMA:
            raise ValueError("invalid Tetragon state schema")
        if context is not None and value.get("context")!=context:
            raise ValueError("Tetragon state belongs to a different Kubernetes context")
        written=datetime.fromisoformat(value["generated_at"].replace("Z","+00:00"))
        if written.tzinfo is None:
            raise ValueError("Tetragon state timestamp requires timezone")
        age=((now or datetime.now(timezone.utc))-written).total_seconds()
        for key in ("policies","capabilities","transitions","verifications"):
            if not isinstance(value.get(key),list):
                raise ValueError("Tetragon state contains an invalid collection")
        result.update({key:value[key] for key in ("generated_at","context","policies","capabilities","transitions","verifications")})
        result["verifications"] = [reassess_record(record) for record in value["verifications"] if isinstance(record,dict)]
        if not isinstance(value.get("agents", []), list):
            raise ValueError("invalid Tetragon agent status")
        result["agents"] = value.get("agents", [])
        result["fresh"]=-5<=age<=stale_after_s and value.get("state")=="ok"
        result["state"]="stale" if age>stale_after_s or age < -5 else value.get("state","error")
        result["error"]=value.get("error")
    except (OSError,ValueError,KeyError,TypeError,AttributeError,RecursionError):
        result.update(state="error",error="Tetragon state could not be validated")
    return result
