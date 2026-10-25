"""두 JSON 문서의 필드 단위 차이. 경로는 JSON Pointer 형식이다."""

from __future__ import annotations

from typing import Any

_MISSING = object()


def _escape(key: str) -> str:
    return key.replace("~", "~0").replace("/", "~1")


def json_diff(before: Any, after: Any, path: str = "") -> list[dict[str, Any]]:
    if isinstance(before, dict) and isinstance(after, dict):
        out: list[dict[str, Any]] = []
        for key in sorted(set(before) | set(after)):
            sub = f"{path}/{_escape(str(key))}"
            b, a = before.get(key, _MISSING), after.get(key, _MISSING)
            if b is _MISSING:
                out.append({"path": sub, "change": "added", "before": None, "after": a})
            elif a is _MISSING:
                out.append({"path": sub, "change": "removed", "before": b, "after": None})
            else:
                out.extend(json_diff(b, a, sub))
        return out
    if isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        out = []
        for i, (b, a) in enumerate(zip(before, after, strict=True)):
            out.extend(json_diff(b, a, f"{path}/{i}"))
        return out
    if before == after and type(before) is type(after):
        return []
    return [{"path": path or "/", "change": "changed", "before": before, "after": after}]
