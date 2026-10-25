"""완성 시점의 update 모드 보고서 목업을 만든다. 값은 가상이며 시험 결과가 아니다. 저장소(.guard)에 넣지 않는 문서용 파일이다.
사용: uv run python tools/make_mockup_report.py  → docs/mockup/report_mockup.json
(HTML 보고서는 2026-10-25 byul 통합 때 없앴다. 화면은 byul platform이 이 JSON을 읽어 보여준다.)"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

src = json.loads((ROOT / "out/5b/exposed/report.json").read_text(encoding="utf-8"))
meta = {r["check_id"]: r for r in src["by_check"]}


def ref(kind, i, rev=1):
    return {"kind": kind, "id": i, "revision": rev, "sha256": hashlib.sha256(f"{kind}{i}".encode()).hexdigest()}


def side(sec, fn="PASS", perf="UNKNOWN", lvl="EXAMPLE", rid=None):
    return {"result": ref("RES", rid) if rid else None, "security": sec, "functional": fn, "performance": perf,
            "verification_level": lvl}


# check_id: (기준 보안, 후보 보안, 비교, 표시)
S = {
    "REQ-01": ("PASS", "FAIL", "REGRESSED", {"fs": {"probed": 15, "of": 15, "label": "기능 확인 범위: 15 / 등록 경로 15"}}),
    "REQ-02": ("PASS", "PASS", "NO_CHANGE", {}),
    "REQ-03": ("FAIL", "PASS", "FIXED", {}),
    "REQ-04": ("PASS", "PASS", "NO_CHANGE", {}),
    "REQ-05": ("FAIL", "FAIL", "STILL_FAILING", {}),
    "REQ-06": ("PASS", "PASS", "NO_CHANGE", {}),
    "PLT-01": ("PASS", "PASS", "NO_CHANGE", {}),
    "PLT-03": ("PASS", "PASS", "NO_CHANGE", {}),
    "PLT-04": ("PASS", "FAIL", "REGRESSED", {}),
    "PLT-06": ("UNKNOWN", "UNKNOWN", "UNKNOWN", {"gaps": ["NO_ENV_FACT"]}),
    "PLT-07": ("PASS", "PASS", "NO_CHANGE", {}),
    "PLT-08": ("PASS", "PASS", "NO_CHANGE", {}),
    "LIF-01": ("PASS", "PASS", "NO_CHANGE", {}),
    "LIF-03": ("FAIL", "FAIL", "STILL_FAILING", {"exception": True}),
    "LIF-07": ("PASS", "PASS", "NO_CHANGE", {}),
    "LIF-10": ("PASS", "PASS", "NO_CHANGE", {}),
    "LIF-11": ("PASS", "UNKNOWN", "UNKNOWN", {"gaps": ["NO_FIXTURE"]}),
    "OBS-03": ("PASS", "PASS", "NO_CHANGE", {}),
    "OBS-04": ("PASS", "PASS", "NO_CHANGE", {}),
    "OBS-05": ("PASS", "PASS", "NO_CHANGE", {}),
    "OBS-06": ("FAIL", "PASS", "FIXED", {}),
    "OBS-09": ("PASS", "PASS", "NO_CHANGE", {"manual": True}),
    "OBS-12": ("PASS", "PASS", "NO_CHANGE", {"manual": True}),
}
rows = []
for cid, (b, c, cmp_, extra) in S.items():
    m = meta[cid]
    manual = extra.get("manual")
    lvl = "DECLARED" if manual else "EXAMPLE"  # 목업은 실측이 아니므로 MEASURED를 쓰지 않는다(INV-02)
    row = {
        "check_id": cid, "purpose": m["purpose"], "area": m["area"], "question": m["question"],
        "review_order": m["review_order"], "impact": None, "applicability": "APPLICABLE", "selected": True,
        "tested_while_undetermined": False, "single": None, "comparison": cmp_,
        "baseline": side(b, lvl=lvl, rid=f"{cid}-B"), "candidate": side(c, lvl=lvl, rid=f"{cid}-C"),
        "gaps": extra.get("gaps", []), "evidence_source": "manual" if manual else "automated", "replaced": None,
        "exception": None, "functional_scope": extra.get("fs"),
    }
    if extra.get("exception"):
        row["exception"] = {"status": "APPROVED", "reason": "다음 분기 게이트웨이 도입 전까지 임시 허용",
                            "approved_by": "정보보호팀", "expires_at": "2026-12-31T00:00:00Z"}
    rows.append(row)

areas = {}
for r in rows:
    areas.setdefault(r["area"], []).append(r["candidate"]["security"])
verdict = lambda vs: "FAIL" if "FAIL" in vs else "UNKNOWN" if "UNKNOWN" in vs else "PASS"
by_area = {a: {"security": verdict(v), "inventory_complete": False} for a, v in areas.items()}
purposes = {}
for r in rows:
    purposes.setdefault(r["purpose"], []).append(r["candidate"]["security"])
by_purpose = {p: {"security": verdict(v), "inventory_complete": True} for p, v in purposes.items()}
sel = {r["check_id"] for r in rows}
not_sel = [{"check_id": k, "reason": "POLICY_SCOPE"} for k in meta if k not in sel and k.startswith(("SUP", "REQ", "PLT", "LIF", "OBS"))]
manual_req = [k for k in meta if k not in sel][:0] + ["SUP-01", "SUP-02", "PLT-02", "LIF-13"]
doc = {
    "kind": "RPT", "id": "RPT-01M7Q3X8N2K4V9T6B5D1C0HJ8R", "revision": 1, "created_at": "2026-11-30T09:00:00Z",
    "mockup": True,  # 화면 설계용 가상 보고서. 시험 결과·승인 기록이 아니다
    "data_origin": "EXAMPLE", "content_sha256": hashlib.sha256(b"mockup").hexdigest(), "refs": [], "mode": "update",
    "official": False, "pack_review_status": "REVIEWED", "policy_status": "CONFIRMED",
    "inputs_manifest": [ref("ENV", "ENV-01M7Q2A4F6H8J0K2M4P6R8T0V2"), ref("ENV", "ENV-01M7Q2B5G7J9K1M3N5Q7S9U1W3"), ref("POL", "POL-01M7Q2C6H8K0M2N4P6R8T0V2X4"),
                        ref("PLAN", "PLAN-01M7Q2D7J9M1N3P5Q7S9U1W3Y5"), ref("PACK", "PACK-BASELINE", 5)],
    "by_check": rows, "by_purpose": by_purpose, "by_area": by_area,
    "not_selected": [n for n in not_sel if n["check_id"] not in manual_req],
    "manual_required": manual_req,
    "limitations": ["목업: 화면 설계용 가상 자료다. 판정·수치·승인자는 실제 시험·승인 기록이 아니다",
                    "점검 목록 미완결 영역 4개 — 집계 PASS 불가",
                    "실행 환경: GPU 1장, Linux, Docker 27 (단일 노드). 다중 노드 구성은 시험하지 않음",
                    "수동 입력 증거는 인증되지 않은 자기 신고(DECLARED)"],
    "approval": {"approved_by": "(목업 예시 승인자)", "approved_at": "2026-11-30T14:20:00Z", "decision": "ACCEPT", "note": "목업 예시: REQ-01·PLT-04 후퇴 건은 후보 배포 보류"},
    "evidence_counts": {"REQ": {"manual": 0, "automated": 6, "not_run": 15}, "PLT": {"manual": 0, "automated": 6, "not_run": 6},
                        "LIF": {"manual": 0, "automated": 5, "not_run": 11}, "OBS": {"manual": 2, "automated": 4, "not_run": 6},
                        "SUP": {"manual": 0, "automated": 0, "not_run": 16}},
}
out = ROOT / "docs/mockup"
out.mkdir(parents=True, exist_ok=True)
(out / "report_mockup.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
print(out / "report_mockup.json")
