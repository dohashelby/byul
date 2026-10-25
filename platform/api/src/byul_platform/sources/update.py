"""update 보고서(RPT JSON) 읽기 → verification-result 변환.

판정·집계는 다시 계산하지 않는다. RPT 값을 계약 모양으로 옮기기만 한다.
RPT에 없는 값은 만들지 않고, 계약이 값을 요구하는데 RPT에 없으면 UNKNOWN/null로 둔다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

VR_SCHEMA = "byul-verification-result/0.2"
_SIDES = ("single", "baseline", "candidate")


def _refs(rpt: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [r for r in rpt.get("refs", []) if isinstance(r, dict) and r.get("kind") == kind]


def _first(items: list[Any]) -> Any:
    return items[0] if items else None


def _side(side: dict[str, Any]) -> dict[str, Any]:
    return {
        "result_ref": side.get("result"),
        "security": side.get("security"),
        # RPT에 기능·성능 축 판정이 없으면 null로 둔다(0.2). 0.1은 계약이 판정을 요구해 UNKNOWN으로 채웠다.
        "functional": side.get("functional"),
        "performance": side.get("performance"),
        "verification_level": side.get("verification_level"),
    }


def _axis(summary: Any) -> dict[str, Any]:
    return {
        name: {"security": value.get("security"), "inventory_complete": value.get("inventory_complete")}
        for name, value in (summary or {}).items()
        if isinstance(value, dict)
    }


def _or_none(value: Any, keys: tuple[str, ...]) -> dict[str, Any] | None:
    return {k: value.get(k) for k in keys} if isinstance(value, dict) else None


def _check(row: dict[str, Any], not_selected: dict[str, str]) -> dict[str, Any]:
    sides = {name: _side(row[name]) for name in _SIDES if isinstance(row.get(name), dict)}
    out: dict[str, Any] = {
        "check_id": row.get("check_id"),
        "area": row.get("area"),
        "purpose": row.get("purpose"),
        "question": row.get("question"),
        "review_order": row.get("review_order"),
        "impact": row.get("impact"),
        "applicability": row.get("applicability"),
        "selected": row.get("selected"),
        "tested_while_undetermined": bool(row.get("tested_while_undetermined")),
        "sides": sides,
        "comparison": row.get("comparison"),
        "functional_comparison": row.get("functional_comparison"),
        "comparison_reasons": list(row.get("comparison_reasons") or []),
        "gaps": list(row.get("gaps") or []),
        "evidence_source": row.get("evidence_source"),
        "exception": _or_none(row.get("exception"), ("status", "reason", "expires_at")),
        "replaced": _or_none(row.get("replaced"), ("source", "revision", "executor", "reason")),
        "functional_scope": _or_none(row.get("functional_scope"), ("probed", "of", "label")),
    }
    if str(row.get("check_id")) in not_selected:
        out["not_selected_reason"] = not_selected[str(row.get("check_id"))]
    return out


def rpt_to_verification_result(rpt: dict[str, Any]) -> dict[str, Any]:
    pack = _first([r for r in rpt.get("inputs_manifest", []) if isinstance(r, dict) and r.get("kind") == "PACK"]) or {}
    not_selected_rows = [i for i in rpt.get("not_selected", []) if isinstance(i, dict)]
    not_selected = {str(i.get("check_id")): str(i.get("reason")) for i in not_selected_rows}
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
        "official": rpt.get("official"),
        "policy_status": rpt.get("policy_status"),
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
        "inputs_manifest": [
            {k: i.get(k) for k in ("kind", "id", "revision", "sha256")}
            for i in rpt.get("inputs_manifest", []) if isinstance(i, dict)
        ],
        "summary": {
            "by_area": _axis(rpt.get("by_area")),
            "by_purpose": _axis(rpt.get("by_purpose")),
            "evidence_counts": rpt.get("evidence_counts") or {},
        },
        "checks": [_check(row, not_selected) for row in rpt.get("by_check", []) if isinstance(row, dict)],
        "manual_required": list(rpt.get("manual_required") or []),
        "not_selected": [{"check_id": i.get("check_id"), "reason": i.get("reason")} for i in not_selected_rows],
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


def _read_rpt_dir(directory: Path) -> list[ReportEntry]:
    """폴더(하위 포함)의 RPT JSON을 읽는다. RPT가 아닌 JSON은 건너뛴다."""
    out: list[ReportEntry] = []
    for path in sorted(directory.rglob("*.json")):
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


class UpdateSource(Protocol):
    mode: str
    detail: str | None

    def reports(self) -> list[ReportEntry]: ...


class ExampleUpdateSource:
    """예시 모드. 예시 폴더에 update-reports/(RPT 원본, 데모 자료)가 있으면 그것을, 없으면 계약 예시를 읽는다."""

    mode = "example"

    def __init__(self, examples_dir: Path) -> None:
        self.rpt_dir = examples_dir / "update-reports"
        self.path = examples_dir / "verification-result.json"
        self.detail = str(self.rpt_dir if self.rpt_dir.is_dir() else self.path)

    def reports(self) -> list[ReportEntry]:
        if self.rpt_dir.is_dir():
            return _read_rpt_dir(self.rpt_dir)
        data = json.loads(self.path.read_text(encoding="utf-8"))
        return [ReportEntry(item, None, None) for item in (data if isinstance(data, list) else [data])]


class ReportDirUpdateSource:
    """UPDATE_REPORTS_DIR 아래(하위 폴더 포함)의 RPT JSON을 읽는다. RPT가 아닌 JSON은 건너뛴다."""

    mode = "live"

    def __init__(self, reports_dir: Path) -> None:
        self.dir = reports_dir
        self.detail = str(reports_dir)

    def reports(self) -> list[ReportEntry]:
        return _read_rpt_dir(self.dir) if self.dir.is_dir() else []


# --- 보고서 화면용 투영 -----------------------------------------------------------
# 계약 verification-result(0.2)만 읽어 보고서 화면 자료를 만든다(RPT 원본 형식은 모른다).
# 판정 값은 계약 그대로이고, 아래 셋만 update HTML 보고서(guard/report/html.py, 2026-10-25 삭제)의
# 표시 규칙을 그대로 옮겼다: 대표 상태(headline), 종합 판정(overall: 영역 판정을 SPEC §4.7 집계 순서로),
# 비공식 사유. 삭제 전 render_html과 24사례 대조로 같음을 확인했다.

_VERDICTS = ("FAIL", "UNKNOWN", "PASS", "NOT_APPLICABLE")


def _headline(check: dict[str, Any], update: bool) -> str:
    """행의 대표 보안 판정. update 모드는 후보 쪽, audit 모드는 single 쪽(html.py `_headline`)."""
    side = check["sides"].get("candidate" if update else "single")
    return (side or {}).get("security") or "UNKNOWN"


def _overall(by_area: dict[str, Any]) -> str:
    verdicts = [s.get("security") for s in by_area.values()]
    for v in ("FAIL", "UNKNOWN", "PASS"):
        if v in verdicts:
            return v
    return "NOT_APPLICABLE"


def _unofficial_reasons(vr: dict[str, Any]) -> list[str]:
    if vr["official"]:
        return []
    why = []
    if vr["policy_status"] != "CONFIRMED":
        why.append("POLICY_DRAFT")
    if vr["pack"]["review_status"] != "REVIEWED":
        why.append("PACK_DRAFT")
    if vr["data_origin"] == "EXAMPLE":
        why.append("EXAMPLE_DATA")
    return why or ["CONDITIONS_UNMET"]


def _side_view(side: dict[str, Any] | None) -> dict[str, Any] | None:
    if side is None:
        return None
    res = side.get("result_ref")
    return {
        "result_id": res.get("id") if res else None,
        "result_revision": res.get("revision") if res else None,
        "security": side.get("security"),
        "functional": side.get("functional"),
        "performance": side.get("performance"),
        "verification_level": side.get("verification_level"),
    }


def _marks(check: dict[str, Any]) -> list[dict[str, Any]]:
    """html.py `_marks`와 같은 표시 항목을 구조로 낸다(한국어 문구는 화면에서)."""
    marks: list[dict[str, Any]] = [{"kind": "gap", "value": g} for g in check["gaps"]]
    if check["evidence_source"] == "manual":
        marks.append({"kind": "manual_declared"})
    if check["replaced"]:
        marks.append({"kind": "replaced", **check["replaced"]})
    if check["functional_scope"]:
        marks.append({"kind": "functional_scope", **check["functional_scope"]})
    if check["exception"]:
        marks.append({"kind": "exception", **check["exception"]})
    if check["tested_while_undetermined"]:
        marks.append({"kind": "tested_while_undetermined"})
    return marks


def report_view(vr: dict[str, Any]) -> dict[str, Any]:
    """계약 검증을 통과한 verification-result(0.2)에서 보고서 화면 자료를 만든다."""
    update = vr["mode"] == "update"
    selected = [c for c in vr["checks"] if c["selected"]]
    heads = {c["check_id"]: _headline(c, update) for c in selected}
    regressed = [c["check_id"] for c in selected if update and c["comparison"] == "REGRESSED"]
    by_area = vr["summary"]["by_area"]
    by_purpose = vr["summary"]["by_purpose"]
    return {
        "report_id": vr["report_ref"]["id"],
        "revision": vr["report_ref"]["revision"],
        "content_sha256": vr["report_ref"]["sha256"],
        "created_at": vr["created_at"],
        "mode": vr["mode"],
        "data_origin": vr["data_origin"],
        "official": vr["official"],
        "policy_status": vr["policy_status"],
        "pack_review_status": vr["pack"]["review_status"],
        "unofficial_reasons": _unofficial_reasons(vr),
        "overall": _overall(by_area),
        "counts": {
            "selected": len(selected),
            **{v.lower(): sum(1 for h in heads.values() if h == v) for v in _VERDICTS},
            "manual_required": len(vr["manual_required"]),
            "not_selected": len(vr["not_selected"]),
            "regressed": len(regressed),
        },
        "incomplete_areas": [a for a, s in by_area.items() if not s.get("inventory_complete")],
        "focus": {
            "regressed": regressed,
            "fail": [cid for cid, h in heads.items() if h == "FAIL" and cid not in regressed],
            "unknown": [cid for cid, h in heads.items() if h == "UNKNOWN"],
        },
        "by_area": [{"name": a, **s} for a, s in by_area.items()],
        "by_purpose": [{"name": a, **s} for a, s in by_purpose.items()],
        "evidence_counts": vr["summary"]["evidence_counts"],
        "limitations": list(vr["limitations"]),
        "manual_required": list(vr["manual_required"]),
        "not_selected": list(vr["not_selected"]),
        "approval": vr["approval"],
        "inputs_manifest": list(vr["inputs_manifest"]),
        "checks": [
            {
                "check_id": c["check_id"],
                "area": c["area"],
                "purpose": c["purpose"],
                "question": c["question"],
                "review_order": c["review_order"],
                "impact": c["impact"],
                "applicability": c["applicability"],
                "selected": c["selected"],
                "headline": heads.get(c["check_id"]),
                "single": _side_view(c["sides"].get("single")),
                "baseline": _side_view(c["sides"].get("baseline")),
                "candidate": _side_view(c["sides"].get("candidate")),
                "comparison": c["comparison"],
                "functional_comparison": c["functional_comparison"],
                "comparison_reasons": list(c["comparison_reasons"]),
                "evidence_source": c["evidence_source"],
                "marks": _marks(c),
            }
            for c in vr["checks"]
        ],
    }
