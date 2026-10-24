"""RPT(JSON) → HTML. JSON에 없는 내용은 넣지 않는다. 한국어 표시는 여기서만 바꾼다."""

from __future__ import annotations

from html import escape
from typing import Any

VERDICT_KO = {"PASS": "충족", "FAIL": "미충족", "UNKNOWN": "미확인", "NOT_APPLICABLE": "해당 없음"}
APPLICABILITY_KO = {"APPLICABLE": "적용", "NOT_APPLICABLE": "해당 없음", "UNDETERMINED": "적용 미정", "CONFLICT": "값 충돌"}
COMPARISON_KO = {
    "NO_CHANGE": "변화 없음", "REGRESSED": "후퇴", "FIXED": "개선", "STILL_FAILING": "계속 미충족",
    "UNKNOWN": "미확인", "NOT_COMPARABLE": "비교 불가",
}
GAP_KO = {
    "NO_EXECUTOR": "실행기 없음", "NO_POLICY_VALUE": "정책 값 없음", "NO_FIXTURE": "시험 자료 없음",
    "NO_ENV_FACT": "환경 사실 없음", "ADAPTER_UNSUPPORTED": "어댑터 미지원",
}
NOT_SELECTED_KO = {"NOT_APPLICABLE": "해당 없음", "POLICY_SCOPE": "정책 범위 밖", "RETIRED": "폐기된 점검"}

_CSS = """
:root { --bg:#f6f7f9; --card:#fff; --fg:#1d1d1f; --muted:#666; --line:#e2e4e8; --pass:#1a7f37; --fail:#cf222e; --unk:#9a6700; --na:#57606a;
  --pass-bg:#e6f4ea; --fail-bg:#fdeaec; --unk-bg:#fff4d6; --na-bg:#eef0f2; --warn-bg:#fff8c5; }
@media (prefers-color-scheme: dark) { :root { --bg:#0d1117; --card:#161b22; --fg:#e6edf3; --muted:#8b949e; --line:#30363d; --pass:#3fb950; --fail:#f85149;
  --unk:#d29922; --na:#8b949e; --pass-bg:#12261a; --fail-bg:#3a1418; --unk-bg:#3b2e00; --na-bg:#21262d; --warn-bg:#3b2e00; } }
* { box-sizing:border-box; }
body { background:var(--bg); color:var(--fg); font:14px/1.55 system-ui, sans-serif; margin:0 auto; max-width:1000px; padding:20px 16px 48px; }
h1 { font-size:20px; margin:0 0 4px; } h2 { font-size:15px; margin:28px 0 10px; }
code { font-size:12px; } .muted { color:var(--muted); } .small { font-size:12px; }
.banner { background:var(--warn-bg); border:1px solid var(--unk); padding:8px 12px; border-radius:8px; font-weight:600; margin:12px 0; }
.hero { display:flex; gap:16px; align-items:center; background:var(--card); border:1px solid var(--line); border-left-width:8px; border-radius:10px; padding:16px 18px; margin:14px 0; }
.hero.s-PASS { border-left-color:var(--pass); } .hero.s-FAIL { border-left-color:var(--fail); }
.hero.s-UNKNOWN { border-left-color:var(--unk); } .hero.s-NOT_APPLICABLE { border-left-color:var(--na); }
.hero .big { font-size:26px; font-weight:700; white-space:nowrap; } .hero p { margin:2px 0 0; }
.tiles { display:grid; grid-template-columns:repeat(auto-fit, minmax(120px,1fr)); gap:10px; margin:12px 0; }
.tile { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:10px 12px; }
.tile b { display:block; font-size:24px; line-height:1.2; } .tile span { font-size:12px; color:var(--muted); }
.tile.t-FAIL b { color:var(--fail); } .tile.t-PASS b { color:var(--pass); } .tile.t-UNKNOWN b { color:var(--unk); }
.card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:10px 14px; margin:8px 0; }
.card.c-FAIL { border-left:5px solid var(--fail); } .card.c-UNKNOWN { border-left:5px solid var(--unk); } .card.c-PASS { border-left:5px solid var(--pass); }
.card .q { margin:2px 0 6px; font-weight:600; }
.chip { display:inline-block; padding:1px 8px; border-radius:999px; font-size:12px; font-weight:600; margin:0 4px 2px 0; }
.chip.v-PASS { background:var(--pass-bg); color:var(--pass); } .chip.v-FAIL { background:var(--fail-bg); color:var(--fail); }
.chip.v-UNKNOWN { background:var(--unk-bg); color:var(--unk); } .chip.v-NOT_APPLICABLE { background:var(--na-bg); color:var(--na); }
.chip.plain { background:var(--na-bg); color:var(--fg); font-weight:400; }
.v-PASS { color:var(--pass); font-weight:600; } .v-FAIL { color:var(--fail); font-weight:600; }
.v-UNKNOWN { color:var(--unk); } .v-NOT_APPLICABLE { color:var(--na); }
.marks { margin:6px 0 0; padding-left:18px; color:var(--muted); font-size:12px; }
details { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:8px 14px; margin:10px 0; }
summary { cursor:pointer; font-weight:600; } details[open] > summary { margin-bottom:8px; }
table { border-collapse:collapse; width:100%; } th, td { border-bottom:1px solid var(--line); padding:5px 6px; text-align:left; vertical-align:top; }
th { font-size:12px; color:var(--muted); font-weight:600; } .wrap { overflow-x:auto; }
ul.plain { margin:4px 0; padding-left:18px; }
@media (max-width:560px) { .hero { flex-direction:column; align-items:flex-start; } }
"""

def _v(verdict: str | None) -> str:
    if verdict is None:
        return '<span class="muted">-</span>'
    return f'<span class="v-{escape(verdict)}">{escape(VERDICT_KO.get(verdict, verdict))}</span>'


def _chips(side: dict | None) -> str:
    if not side:
        return '<span class="muted">-</span>'
    out = [f'<span class="chip v-{escape(side["security"])}">보안 {escape(VERDICT_KO.get(side["security"], side["security"]))}</span>']
    for key, label in (("functional", "기능"), ("performance", "성능")):
        if side.get(key):
            out.append(f'<span class="chip v-{escape(side[key])}">{label} {escape(VERDICT_KO.get(side[key], side[key]))}</span>')
    return "".join(out)


def _marks(row: dict) -> list[str]:
    marks = [escape(GAP_KO.get(g, g)) for g in row["gaps"]]
    if row.get("evidence_source") == "manual":
        marks.append("수동 입력(DECLARED, 자기 신고)")
    if row.get("replaced"):
        rp = row["replaced"]
        src = {"automated": "자동", "manual": "수동"}.get(rp["source"], rp["source"])
        marks.append(f"대체됨: 이전 {src} 증거(rev {rp['revision']}) — {escape(rp.get('reason') or '')}")
    if row.get("functional_scope"):
        marks.append(escape(row["functional_scope"]["label"]))
    if row.get("exception"):
        e = row["exception"]
        marks.append(f"예외 승인됨 (만료 {escape(e['expires_at'] or '-')}, {escape(e['reason'])})")
    if row.get("tested_while_undetermined"):
        marks.append("적용 미정 상태로 시험함")
    return marks


def _headline(row: dict, update: bool) -> str:
    """행 하나의 대표 상태. update 모드는 비교 결과가 있으면 그것, 없으면 후보 쪽 보안."""
    if update:
        return (row.get("candidate") or {}).get("security") or "UNKNOWN"
    return (row.get("single") or {}).get("security") or "UNKNOWN"


def _card(row: dict, update: bool, status: str) -> str:
    h = [f'<div class="card c-{escape(status)}"><code>{escape(row["check_id"])}</code> '
         f'<span class="muted small">{escape(row["purpose"])}</span>',
         f'<p class="q">{escape(row["question"])}</p>']
    if update:
        cmp_ = row.get("comparison")
        if cmp_:
            h.append(f'<span class="chip plain">버전 비교 {escape(COMPARISON_KO.get(cmp_, cmp_))}</span>')
        h.append('<div class="small muted">기준</div>' + _chips(row.get("baseline")))
        h.append('<div class="small muted">후보</div>' + _chips(row.get("candidate")))
    else:
        h.append(_chips(row.get("single")))
    marks = _marks(row)
    if marks:
        h.append('<ul class="marks">' + "".join(f"<li>{m}</li>" for m in marks) + "</ul>")
    h.append("</div>")
    return "".join(h)


def render_html(report: dict[str, Any]) -> str:
    r = report
    update = r["mode"] == "update"
    rows = [row for row in r["by_check"] if row["selected"]]
    heads = [(row, _headline(row, update)) for row in rows]
    n = {k: sum(1 for _, h in heads if h == k) for k in ("FAIL", "UNKNOWN", "PASS", "NOT_APPLICABLE")}
    regressed = [row for row in rows if update and row.get("comparison") == "REGRESSED"]
    area_verdicts = [s["security"] for s in r["by_area"].values()]
    overall = ("FAIL" if "FAIL" in area_verdicts else "UNKNOWN" if "UNKNOWN" in area_verdicts
               else "PASS" if "PASS" in area_verdicts else "NOT_APPLICABLE")
    incomplete = [a for a, s in r["by_area"].items() if not s.get("inventory_complete")]
    out: list[str] = []
    w = out.append
    w("<!doctype html><html lang='ko'><head><meta charset='utf-8'>")
    w("<meta name='viewport' content='width=device-width, initial-scale=1'>")
    w(f"<title>점검 보고서 {escape(r['id'])}</title><style>{_CSS}</style></head><body>")
    w(f"<h1>서빙엔진 보안 점검 보고서</h1><p class='muted small'>{escape(r['created_at'])} · "
      f"{'버전 비교(update)' if update else '단일 점검(audit)'} · 자료 출처 {escape(r['data_origin'])}</p>")

    if not r["official"]:
        why = []
        if r["policy_status"] != "CONFIRMED":
            why.append("정책 DRAFT")
        if r["pack_review_status"] != "REVIEWED":
            why.append("검토 전 팩")
        if r["data_origin"] == "EXAMPLE":
            why.append("EXAMPLE 자료")
        w(f"<p class='banner'>공식 아님 — {escape(', '.join(why) or '조건 미충족')}</p>")

    # 1. 한눈에: 종합 + 숫자
    summary = f"선정 {len(rows)}건 중 미충족 {n['FAIL']}건 · 미확인 {n['UNKNOWN']}건 · 충족 {n['PASS']}건"
    w(f"<div class='hero s-{overall}'><div class='big v-{overall}'>{escape(VERDICT_KO[overall])}</div><div>"
      f"<strong>종합 보안 판정</strong><p>{escape(summary)}</p>")
    if incomplete:
        w(f"<p class='muted small'>점검 목록 미완결: {escape(', '.join(incomplete))}</p>")
    w("</div></div>")
    w("<div class='tiles'>")
    w(f"<div class='tile t-FAIL'><b>{n['FAIL']}</b><span>미충족</span></div>")
    w(f"<div class='tile t-UNKNOWN'><b>{n['UNKNOWN']}</b><span>미확인</span></div>")
    w(f"<div class='tile t-PASS'><b>{n['PASS']}</b><span>충족</span></div>")
    w(f"<div class='tile'><b>{len(r['manual_required'])}</b><span>수동 시험 필요</span></div>")
    w(f"<div class='tile'><b>{len(r['not_selected'])}</b><span>미선정(범위 밖)</span></div>")
    if update:
        w(f"<div class='tile t-FAIL'><b>{len(regressed)}</b><span>후퇴(기준 충족→후보 미충족)</span></div>")
    w("</div>")

    # 2. 주요 결과
    focus_fail = [row for row, h in heads if h == "FAIL"]
    focus_unk = [row for row, h in heads if h == "UNKNOWN"]
    w("<h2>주요 결과</h2>")
    if update and regressed:
        w(f"<p><strong>후퇴 {len(regressed)}건</strong> <span class='muted small'>(기준 충족 → 후보 미충족)</span></p>")
        for row in regressed:
            w(_card(row, update, "FAIL"))
        focus_fail = [row for row in focus_fail if row not in regressed]
    if focus_fail:
        w(f"<p><strong>미충족 {len(focus_fail)}건</strong></p>")
        for row in focus_fail:
            w(_card(row, update, "FAIL"))
    if focus_unk:
        w(f"<p><strong>미확인 {len(focus_unk)}건</strong> <span class='muted small'>(근거 부족, 충족으로 집계하지 않음)</span></p>")
        for row in focus_unk:
            w(_card(row, update, "UNKNOWN"))
    if not (focus_fail or focus_unk or regressed):
        w("<p class='muted'>미충족·미확인 항목 없음</p>")

    # 3. 영역·보호 목적
    w("<h2>영역·보호 목적별</h2><div class='wrap'><table><tr><th>구분</th><th>이름</th><th>보안</th><th>점검 목록 완결</th></tr>")
    for kind, mapping in (("영역", r["by_area"]), ("보호 목적", r["by_purpose"])):
        for name, s in mapping.items():
            w(f"<tr><td>{kind}</td><td>{escape(name)}</td><td>{_v(s['security'])}</td>"
              f"<td>{'예' if s.get('inventory_complete') else '아니오'}</td></tr>")
    w("</table></div>")

    counts = r.get("evidence_counts") or {}
    if counts:
        w("<h2>증거 출처</h2><div class='wrap'><table><tr><th>영역</th><th>수동 입력 / 자동 측정</th><th>미실행</th></tr>")
        for area, c in counts.items():
            w(f"<tr><td>{escape(area)}</td><td>수동 입력 {c['manual']}건 / 자동 측정 {c['automated']}건</td><td>{c['not_run']}건</td></tr>")
        w("</table></div>")

    # 4. 한계 (짧게 항상 노출)
    w("<h2>한계</h2><ul class='plain'>")
    for item in r["limitations"]:
        w(f"<li>{escape(item)}</li>")
    w("</ul>")

    # 5. 접어 둔 상세
    w(f"<details><summary>선정된 점검 전체 ({len(rows)}건)</summary><div class='wrap'><table><tr><th>점검</th><th>질문</th>")
    w("<th>기준</th><th>후보</th><th>비교</th>" if update else "<th>결과</th>")
    w("<th>표시</th></tr>")
    for row in rows:
        app = APPLICABILITY_KO.get(row["applicability"], row["applicability"])
        w(f"<tr><td><code>{escape(row['check_id'])}</code><br><span class='muted small'>{escape(row['purpose'])}</span>"
          f"<br><span class='muted small'>{escape(app)}</span></td><td>{escape(row['question'])}</td>")
        if update:
            cmp_ = row.get("comparison")
            w(f"<td>{_chips(row.get('baseline'))}</td><td>{_chips(row.get('candidate'))}</td>"
              f"<td>보안: {escape(COMPARISON_KO.get(cmp_, cmp_) if cmp_ else '-')}"
              f"<br>기능: {escape(COMPARISON_KO.get(row.get('functional_comparison'), '-'))}"
              f"<br>{escape(', '.join(row.get('comparison_reasons') or []))}</td>")
        else:
            w(f"<td>{_chips(row.get('single'))}</td>")
        w(f"<td class='small'>{'<br>'.join(_marks(row)) or '-'}</td></tr>")
    w("</table></div></details>")

    w(f"<details><summary>수동 시험 필요 ({len(r['manual_required'])}건)</summary><p>")
    w(", ".join(f"<code>{escape(c)}</code>" for c in r["manual_required"]) or "없음")
    w("</p></details>")

    by_reason: dict[str, list[str]] = {}
    for ns in r["not_selected"]:
        by_reason.setdefault(ns["reason"], []).append(ns["check_id"])
    w(f"<details><summary>미선정 ({len(r['not_selected'])}건)</summary>")
    if not by_reason:
        w("<p>없음</p>")
    for reason, ids in by_reason.items():
        w(f"<p><strong>{escape(NOT_SELECTED_KO.get(reason, reason))}</strong> ({len(ids)}건)<br>"
          + ", ".join(f"<code>{escape(i)}</code>" for i in ids) + "</p>")
    w("</details>")

    approval = r.get("approval")
    w("<details><summary>승인·추적 정보</summary>")
    if approval:
        w(f"<p>승인: {escape(approval['approved_by'])} · {escape(approval['approved_at'])} · {escape(approval['decision'])}</p>")
    else:
        w("<p class='muted'>승인 기록 없음 (자동 판정은 승인이 아니다)</p>")
    w(f"<p class='muted small'><code>{escape(r['id'])}@{r['revision']}</code> · content_sha256 "
      f"<code>{escape(r['content_sha256'][:16])}…</code></p>")
    w("<div class='wrap'><table><tr><th>종류</th><th>ID</th><th>revision</th><th>sha256</th></tr>")
    for ref in r["inputs_manifest"]:
        w(f"<tr><td>{escape(ref['kind'])}</td><td><code>{escape(ref['id'])}</code></td><td>{ref['revision']}</td>"
          f"<td><code>{escape(ref['sha256'][:16])}…</code></td></tr>")
    w("</table></div></details></body></html>")
    return "\n".join(out)
