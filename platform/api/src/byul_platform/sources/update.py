"""update 보고서(RPT JSON) 읽기 → verification-result 변환.

판정·집계는 다시 계산하지 않는다. RPT 값을 계약 모양으로 옮기기만 한다.
RPT에 없는 값은 만들지 않고, 계약이 값을 요구하는데 RPT에 없으면 UNKNOWN/null로 둔다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

VR_SCHEMA = "byul-verification-result/0.1"
_SIDES = ("single", "baseline", "candidate")


def _refs(rpt: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [r for r in rpt.get("refs", []) if isinstance(r, dict) and r.get("kind") == kind]


def _first(items: list[Any]) -> Any:
    return items[0] if items else None


def _side(side: dict[str, Any]) -> dict[str, Any]:
    return {
        "result_ref": side.get("result"),
        "security": side.get("security"),
        # RPT SideResult의 functional·performance는 비어 있을 수 있다. 계약은 판정을 요구하므로 미확인으로 둔다.
        "functional": side.get("functional") or "UNKNOWN",
        "performance": side.get("performance") or "UNKNOWN",
        "verification_level": side.get("verification_level"),
    }


def _check(row: dict[str, Any], not_selected: dict[str, str]) -> dict[str, Any]:
    sides = {name: _side(row[name]) for name in _SIDES if isinstance(row.get(name), dict)}
    out: dict[str, Any] = {
        "check_id": row.get("check_id"),
        "applicability": row.get("applicability"),
        "selected": row.get("selected"),
        "sides": sides,
        "comparison": row.get("comparison"),
        "gaps": list(row.get("gaps") or []),
        "evidence_source": row.get("evidence_source"),
    }
    if str(row.get("check_id")) in not_selected:
        out["not_selected_reason"] = not_selected[str(row.get("check_id"))]
    exception = row.get("exception")
    if isinstance(exception, dict):
        out["exception_approved"] = exception.get("status") == "APPROVED"
    return out


def rpt_to_verification_result(rpt: dict[str, Any]) -> dict[str, Any]:
    pack = _first([r for r in rpt.get("inputs_manifest", []) if isinstance(r, dict) and r.get("kind") == "PACK"]) or {}
    not_selected = {
        str(i.get("check_id")): str(i.get("reason"))
        for i in rpt.get("not_selected", [])
        if isinstance(i, dict)
    }
    approval = rpt.get("approval")
    return {
        "schema_version": VR_SCHEMA,
        "report_ref": {
            "kind": "RPT",
            "id": rpt.get("id"),
            "revision": rpt.get("revision"),
            "sha256": rpt.get("content_sha256"),
        },
        "mode": rpt.get("mode"),
        "created_at": rpt.get("created_at"),
        "data_origin": rpt.get("data_origin"),
        "plan_ref": _first(_refs(rpt, "PLAN")),
        "environment_refs": _refs(rpt, "ENV"),
        "policy_ref": _first(_refs(rpt, "POL")),
        "byul_policy_ref": None,
        "pack": {
            "id": pack.get("id"),
            "revision": pack.get("revision"),
            "review_status": rpt.get("pack_review_status"),
        },
        "change_ref": _first(_refs(rpt, "CHG")),
        "summary": {
            "by_area": rpt.get("by_area") or {},
            "evidence_counts": rpt.get("evidence_counts") or {},
        },
        "checks": [_check(row, not_selected) for row in rpt.get("by_check", []) if isinstance(row, dict)],
        "limitations": list(rpt.get("limitations") or []),
        "approval": approval if approval is None else {
            "approved_by": approval.get("approved_by"),
            "approved_at": approval.get("approved_at"),
            "decision": approval.get("decision"),
            "note": approval.get("note", ""),
        },
    }


@dataclass
class ReportEntry:
    candidate: Any  # verification-result 후보(검증 전)
    raw_checks: dict[str, Any] | None
    path: str | None


class UpdateSource(Protocol):
    mode: str
    detail: str | None

    def reports(self) -> list[ReportEntry]: ...


class ExampleUpdateSource:
    mode = "example"

    def __init__(self, examples_dir: Path) -> None:
        self.path = examples_dir / "verification-result.json"
        self.detail = str(self.path)

    def reports(self) -> list[ReportEntry]:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        return [ReportEntry(item, None, None) for item in (data if isinstance(data, list) else [data])]


class ReportDirUpdateSource:
    """UPDATE_REPORTS_DIR 아래(하위 폴더 포함)의 RPT JSON을 읽는다. RPT가 아닌 JSON은 건너뛴다."""

    mode = "live"

    def __init__(self, reports_dir: Path) -> None:
        self.dir = reports_dir
        self.detail = str(reports_dir)

    def reports(self) -> list[ReportEntry]:
        out: list[ReportEntry] = []
        if not self.dir.is_dir():
            return out
        for path in sorted(self.dir.rglob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
                if path.name.startswith("RPT-") or path.name == "report.json":
                    out.append(ReportEntry({"_unreadable": str(path), "_error": type(error).__name__}, None, str(path)))
                continue
            if not isinstance(data, dict) or data.get("kind") != "RPT":
                continue
            raw = {str(r.get("check_id")): r for r in data.get("by_check", []) if isinstance(r, dict)}
            out.append(ReportEntry(rpt_to_verification_result(data), raw, str(path)))
        return out
