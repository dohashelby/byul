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
:root { --bg:#fff; --fg:#1d1d1f; --muted:#666; --line:#ddd; --pass:#1a7f37; --fail:#cf222e; --unk:#9a6700; --na:#57606a; --warn-bg:#fff8c5; }
@media (prefers-color-scheme: dark) { :root { --bg:#0d1117; --fg:#e6edf3; --muted:#8b949e; --line:#30363d; --pass:#3fb950; --fail:#f85149; --unk:#d29922; --na:#8b949e; --warn-bg:#3b2e00; } }
body { background:var(--bg); color:var(--fg); font:14px/1.5 system-ui, sans-serif; margin:0 auto; max-width:1100px; padding:16px; }
h1 { font-size:20px; } h2 { font-size:16px; margin-top:28px; border-bottom:1px solid var(--line); }
.banner { background:var(--warn-bg); border:1px solid var(--unk); padding:8px 12px; border-radius:6px; font-weight:600; }
table { border-collapse:collapse; width:100%; } th, td { border-bottom:1px solid var(--line); padding:4px 6px; text-align:left; vertical-align:top; }
.wrap { overflow-x:auto; } .muted { color:var(--muted); } code { font-size:12px; }
.v-PASS { color:var(--pass); font-weight:600; } .v-FAIL { color:var(--fail); font-weight:600; }
.v-UNKNOWN { color:var(--unk); } .v-NOT_APPLICABLE { color:var(--na); }
"""


def _v(verdict: str | None) -> str:
    if verdict is None:
        return '<span class="muted">-</span>'
    return f'<span class="v-{escape(verdict)}">{escape(VERDICT_KO.get(verdict, verdict))}</span>'


def _side_cell(side: dict | None) -> str:
    if not side:
        return '<span class="muted">-</span>'
    parts = [f"보안 {_v(side['security'])}"]
    if side.get("functional"):
        parts.append(f"기능 {_v(side['functional'])}")
    if side.get("performance"):
        parts.append(f"성능 {_v(side['performance'])}")
    level = side.get("verification_level")
    parts.append(f'<span class="muted">검증 수준 {escape(level) if level else "없음"}</span>')
    if side.get("result"):
        r = side["result"]
        parts.append(f'<code class="muted">{escape(r["id"])}@{r["revision"]}</code>')
    return "<br>".join(parts)


def render_html(report: dict[str, Any]) -> str:
    r = report
    out: list[str] = []
    w = out.append
    w("<!doctype html><html lang='ko'><head><meta charset='utf-8'>")
    w("<meta name='viewport' content='width=device-width, initial-scale=1'>")
    w(f"<title>점검 보고서 {escape(r['id'])}</title><style>{_CSS}</style></head><body>")
    w(f"<h1>서빙엔진 보안 점검 보고서</h1><p class='muted'><code>{escape(r['id'])}@{r['revision']}</code> · "
      f"{escape(r['created_at'])} · 모드 {escape(r['mode'])} · 자료 출처 {escape(r['data_origin'])} · "
      f"content_sha256 <code>{escape(r['content_sha256'][:16])}…</code></p>")

    if not r["official"]:
        why = []
        if r["policy_status"] != "CONFIRMED":
            why.append("정책 DRAFT")
        if r["pack_review_status"] != "REVIEWED":
            why.append("검토 전 팩")
        if r["data_origin"] == "EXAMPLE":
            why.append("EXAMPLE 자료")
        w(f"<p class='banner'>공식 아님 — {escape(', '.join(why) or '조건 미충족')}</p>")
    approval = r.get("approval")
    if approval:
        w(f"<p>승인: {escape(approval['approved_by'])} · {escape(approval['approved_at'])} · {escape(approval['decision'])}</p>")
    else:
        w("<p class='muted'>승인 기록 없음 (자동 판정은 승인이 아니다)</p>")

    w("<h2>한계</h2><ul>")
    for item in r["limitations"]:
        w(f"<li>{escape(item)}</li>")
    w("</ul>")

    w("<h2>영역별</h2><div class='wrap'><table><tr><th>영역</th><th>보안</th><th>점검 목록 완결</th></tr>")
    for area, s in r["by_area"].items():
        w(f"<tr><td>{escape(area)}</td><td>{_v(s['security'])}</td><td>{'예' if s.get('inventory_complete') else '아니오'}</td></tr>")
    w("</table></div>")

    w("<h2>보호 목적별</h2><div class='wrap'><table><tr><th>보호 목적</th><th>보안</th><th>점검 목록 완결</th></tr>")
    for purpose, s in r["by_purpose"].items():
        w(f"<tr><td>{escape(purpose)}</td><td>{_v(s['security'])}</td><td>{'예' if s.get('inventory_complete') else '아니오'}</td></tr>")
    w("</table></div>")

    update = r["mode"] == "update"
    w("<h2>점검별</h2><div class='wrap'><table><tr><th>점검</th><th>질문</th><th>적용</th>")
    w("<th>기준</th><th>후보</th><th>비교</th>" if update else "<th>결과</th>")
    w("<th>공백·표시</th></tr>")
    for row in r["by_check"]:
        if not row["selected"]:
            continue
        app = APPLICABILITY_KO.get(row["applicability"], row["applicability"])
        if row.get("tested_while_undetermined"):
            app += " (적용 미정 상태로 시험함)"
        marks = [escape(GAP_KO.get(g, g)) for g in row["gaps"]]
        if row.get("functional_scope"):
            marks.append(escape(row["functional_scope"]["label"]))
        if row.get("exception"):
            e = row["exception"]
            marks.append(f"예외 승인됨 (만료 {escape(e['expires_at'] or '-')}, {escape(e['reason'])})")
        w(f"<tr><td><code>{escape(row['check_id'])}</code><br><span class='muted'>{escape(row['purpose'])}</span></td>"
          f"<td>{escape(row['question'])}</td><td>{escape(app)}</td>")
        if update:
            cmp_ = row.get("comparison")
            w(f"<td>{_side_cell(row.get('baseline'))}</td><td>{_side_cell(row.get('candidate'))}</td>"
              f"<td>보안: {escape(COMPARISON_KO.get(cmp_, cmp_) if cmp_ else '-')}"
              f"<br>기능: {escape(COMPARISON_KO.get(row.get('functional_comparison'), '-'))}"
              f"<br>{escape(', '.join(row.get('comparison_reasons') or []))}</td>")
        else:
            w(f"<td>{_side_cell(row.get('single'))}</td>")
        w(f"<td>{'<br>'.join(marks) or '-'}</td></tr>")
    w("</table></div>")

    w(f"<h2>수동 시험 필요 ({len(r['manual_required'])})</h2><p>")
    w(", ".join(f"<code>{escape(c)}</code>" for c in r["manual_required"]) or "없음")
    w("</p>")

    w(f"<h2>미선정 ({len(r['not_selected'])})</h2><p>")
    w(", ".join(f"<code>{escape(n['check_id'])}</code> {escape(NOT_SELECTED_KO.get(n['reason'], n['reason']))}"
                for n in r["not_selected"]) or "없음")
    w("</p>")

    w("<h2>입력 목록</h2><div class='wrap'><table><tr><th>종류</th><th>ID</th><th>revision</th><th>sha256</th></tr>")
    for ref in r["inputs_manifest"]:
        w(f"<tr><td>{escape(ref['kind'])}</td><td><code>{escape(ref['id'])}</code></td><td>{ref['revision']}</td>"
          f"<td><code>{escape(ref['sha256'][:16])}…</code></td></tr>")
    w("</table></div></body></html>")
    return "\n".join(out)
