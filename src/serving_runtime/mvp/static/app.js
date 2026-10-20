"use strict";

// Read-only dashboard. All server data is rendered with textContent only.
const REFRESH_MS = 3000;
const RESULT_LABELS = {
  ALLOWED: "허용",
  DENIED: "정책 거부",
  INVALID: "입력 오류",
  ERROR: "처리 오류",
  INCOMPLETE: "미완료",
};

const state = { filter: "", selectedId: null, lastSuccess: null };

function $(id) {
  return document.getElementById(id);
}

function el(tag, options = {}, children = []) {
  const node = document.createElement(tag);
  if (options.className) node.className = options.className;
  if (options.text !== undefined && options.text !== null) node.textContent = String(options.text);
  if (options.title) node.title = options.title;
  for (const child of children) node.appendChild(child);
  return node;
}

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

function formatTime(iso) {
  if (!iso) return "–";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return String(iso);
  return date.toLocaleString("ko-KR", { hour12: false });
}

function clockTime(date) {
  return date.toLocaleTimeString("ko-KR", { hour12: false });
}

function badge(result) {
  return el("span", { className: `badge badge-${result}`, text: RESULT_LABELS[result] || result });
}

function setDot(id, ok) {
  const dot = $(id);
  dot.className = "dot" + (ok === true ? " ok" : ok === false ? " bad" : "");
}

function fillKv(id, rows) {
  const list = $(id);
  clear(list);
  for (const [label, value, options = {}] of rows) {
    list.appendChild(el("dt", { text: label }));
    list.appendChild(
      el("dd", {
        className: [options.mono ? "mono" : "", options.problem ? "problem" : ""].join(" ").trim(),
        text: value === null || value === undefined || value === "" ? "–" : value,
        title: options.title,
      }),
    );
  }
}

async function getJson(url) {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) throw new Error(`${url} → HTTP ${response.status}`);
  return response.json();
}

function renderStatus(status) {
  $("session-id").textContent = `관측 세션 ${status.observation_session_id.slice(0, 8)}`;

  const policy = status.policy;
  setDot("policy-dot", policy.valid);
  const policyRows = [
    ["정책", policy.policy_id],
    ["판본", policy.policy_version],
    ["대상", policy.target_id],
    ["SHA-256", policy.sha256 ? `${policy.sha256.slice(0, 12)}…` : null, { mono: true, title: policy.sha256 }],
    ["로딩", formatTime(policy.loaded_at)],
    ["유효", policy.valid ? "유효" : "무효 — 추론 요청은 503", { problem: !policy.valid }],
  ];
  if (!policy.valid) {
    policyRows.push(["사유", `${policy.error_code || ""} ${policy.error_message || ""}`.trim(), { problem: true }]);
  }
  fillKv("policy-kv", policyRows);

  const engine = status.mock_engine;
  const receipts = engine.receipts_store || {};
  setDot("engine-dot", engine.healthy);
  const engineRows = [
    ["주소", engine.url, { mono: true }],
    ["연결", engine.healthy ? "정상" : "응답 없음", { problem: !engine.healthy }],
    ["점검", formatTime(engine.checked_at)],
    ["수신 기록", receipts.ok === false ? `저장 오류: ${receipts.last_error}` : receipts.state || "–", { problem: receipts.ok === false }],
  ];
  if (engine.error) engineRows.push(["오류", engine.error, { problem: true }]);
  engineRows.push(["의미", "연결 점검이며 추론·관측 동작 검증이 아님"]);
  fillKv("engine-kv", engineRows);
}

function renderSummary(summary) {
  for (const key of ["ALLOWED", "DENIED", "INVALID", "ERROR"]) {
    $(`count-${key}`).textContent = summary.counts[key];
  }
  $("count-INCOMPLETE").textContent = summary.incomplete;

  const store = summary.record_store;
  const corrupt = summary.corrupt_lines;
  const corruptDetails = summary.corrupt_details || { gateway: [], engine: [] };
  const corruptTotal = corrupt.gateway + corrupt.engine;
  setDot("store-dot", store.ok && corruptTotal === 0 ? true : store.state === "unknown" ? null : false);
  const rows = [
    ["경로", store.data_dir, { mono: true }],
    ["상태", { ok: "정상", error: "오류", unknown: "미확인" }[store.state] || store.state, { problem: store.state === "error" }],
    ["손상 줄", `Gateway ${corrupt.gateway} · 엔진 ${corrupt.engine}`, { problem: corruptTotal > 0 }],
    ["전체 요청", summary.total],
  ];
  if (store.last_error) rows.push(["마지막 오류", `${formatTime(store.last_error_at)} ${store.last_error}`, { problem: true }]);
  for (const [source, label] of [["gateway", "Gateway 손상"], ["engine", "엔진 손상"]]) {
    if ((corruptDetails[source] || []).length) {
      rows.push([
        label,
        corruptDetails[source].map((item) => `#${item.line}: ${item.reason}`).join(" · "),
        { problem: true },
      ]);
    }
  }
  fillKv("store-kv", rows);
}

function renderRequests(items) {
  const body = $("request-rows");
  clear(body);
  $("empty-state").hidden = items.length > 0;
  $("empty-state").textContent = state.filter
    ? `${RESULT_LABELS[state.filter]} 요청이 없습니다.`
    : "아직 기록된 요청이 없습니다.";
  for (const item of items) {
    const row = el("tr", {}, [
      el("td", { text: formatTime(item.recorded_at) }),
      el("td", { className: "mono", title: item.request_id }, [
        el("span", { text: item.request_id.slice(0, 8) }),
        ...(item.current_session ? [] : [el("span", { className: "badge badge-previous", text: "이전 실행" })]),
      ]),
      el("td", { text: item.model ?? "–" }),
      el("td", { className: "num", text: item.max_tokens ?? "–" }),
      el("td", {}, [badge(item.result)]),
      el("td", { className: "mono muted", text: item.reason_code ?? "–" }),
    ]);
    if (item.request_id === state.selectedId) row.classList.add("is-selected");
    row.addEventListener("click", () => openDetail(item.request_id));
    body.appendChild(row);
  }
}

function section(title, children) {
  const wrap = document.createDocumentFragment();
  wrap.appendChild(el("h4", { text: title }));
  for (const child of children) wrap.appendChild(child);
  return wrap;
}

function kvList(rows) {
  const list = el("dl", { className: "kv" });
  for (const [label, value, options = {}] of rows) {
    list.appendChild(el("dt", { text: label }));
    list.appendChild(
      el("dd", {
        className: options.mono ? "mono" : "",
        text: value === null || value === undefined || value === "" ? "–" : value,
      }),
    );
  }
  return list;
}

function renderDetail(detail) {
  const body = $("drawer-body");
  clear(body);
  const final = detail.final_state || {};
  const finalRecord = [...detail.gateway_records].reverse().find((r) => r.phase === "final")
    || detail.gateway_records[detail.gateway_records.length - 1];
  const decision = finalRecord.decision || {};
  const policy = finalRecord.policy || {};
  const delivery = finalRecord.delivery || {};

  const head = el("div", { className: "detail-head" }, [badge(final.result || "INCOMPLETE")]);
  body.appendChild(head);
  body.appendChild(el("p", { className: "mono", text: detail.request_id }));

  body.appendChild(el("div", { className: `evidence-box ${detail.evidence_state}`, text: detail.evidence_summary }));

  body.appendChild(
    section("판정", [
      kvList([
        ["결과", RESULT_LABELS[final.result] || final.result],
        ["HTTP", decision.http_status],
        ["사유 코드", decision.reason_code, { mono: true }],
        ["설명", decision.message],
        ["적용 규칙", decision.rule_id, { mono: true }],
        ["모델", final.model],
        ["max_tokens", final.max_tokens],
      ]),
    ]),
  );
  body.appendChild(
    section("적용 정책", [
      kvList([
        ["정책", `${policy.policy_id ?? "–"} · 판본 ${policy.policy_version ?? "–"}`],
        ["SHA-256", policy.sha256, { mono: true }],
      ]),
    ]),
  );
  body.appendChild(
    section("전달", [
      kvList([
        ["전달 시도", delivery.attempted ? "예" : "아니요 (엔진 미호출)"],
        ["기록 시점 수신 확인", delivery.backend_receipt],
        ["응답", delivery.response],
        ["오류 코드", delivery.error_code, { mono: true }],
      ]),
    ]),
  );

  const timeline = el("ol", { className: "timeline" });
  for (const record of detail.gateway_records) {
    timeline.appendChild(
      el("li", {}, [
        el("div", { className: "phase", text: record.phase === "started" ? "전달 전 시작 기록" : "최종 기록" }),
        el("div", { className: "muted", text: formatTime(record.recorded_at) }),
      ]),
    );
  }
  body.appendChild(section("Gateway 기록", [timeline]));

  const live = detail.engine_live || {};
  body.appendChild(
    section("엔진 수신 증거", [
      kvList([
        ["수신 기록 파일", detail.engine_receipts.length ? `${detail.engine_receipts.length}건 (${formatTime(detail.engine_receipts[0].received_at)})` : "없음"],
        ["실시간 조회", `${live.status ?? "–"} · ${formatTime(live.queried_at)}`],
        ["같은 관측 세션", detail.same_observation_session ? "예" : "아니요"],
        ["손상 줄", `Gateway ${detail.corrupt_lines.gateway} · 엔진 ${detail.corrupt_lines.engine}`],
      ]),
    ]),
  );

  const download = el("a", { className: "download", text: "증거 JSON 내려받기" });
  download.href = `/api/requests/${encodeURIComponent(detail.request_id)}/evidence`;
  download.setAttribute("download", `evidence-${detail.request_id}.json`);
  body.appendChild(download);

  const raw = el("details", {}, [
    el("summary", { text: "원본 기록 보기" }),
    el("pre", { text: JSON.stringify(detail, null, 2) }),
  ]);
  body.appendChild(raw);
}

async function loadDetail() {
  if (!state.selectedId) return;
  try {
    renderDetail(await getJson(`/api/requests/${encodeURIComponent(state.selectedId)}`));
  } catch (error) {
    const body = $("drawer-body");
    clear(body);
    body.appendChild(el("div", { className: "evidence-box unknown", text: `상세를 불러오지 못했습니다: ${error.message}` }));
  }
}

function openDetail(requestId) {
  state.selectedId = requestId;
  $("drawer").classList.add("is-open");
  $("drawer").setAttribute("aria-hidden", "false");
  $("drawer-backdrop").hidden = false;
  for (const row of $("request-rows").children) {
    row.classList.toggle("is-selected", row.children[1].title === requestId);
  }
  loadDetail();
}

function closeDetail() {
  state.selectedId = null;
  $("drawer").classList.remove("is-open");
  $("drawer").setAttribute("aria-hidden", "true");
  $("drawer-backdrop").hidden = true;
  for (const row of $("request-rows").children) row.classList.remove("is-selected");
}

function setFilter(filter) {
  state.filter = filter;
  for (const button of document.querySelectorAll(".filter")) {
    button.classList.toggle("is-active", button.dataset.filter === filter);
  }
  refresh();
}

async function refresh() {
  const query = state.filter ? `?result=${encodeURIComponent(state.filter)}` : "";
  try {
    const [status, summary, list] = await Promise.all([
      getJson("/api/status"),
      getJson("/api/summary"),
      getJson(`/api/requests${query}`),
    ]);
    renderStatus(status);
    renderSummary(summary);
    renderRequests(list.requests);
    state.lastSuccess = new Date();
    document.body.classList.remove("is-stale");
    $("stale-banner").hidden = true;
    $("refresh-state").textContent = `3초마다 갱신 · 마지막 ${clockTime(state.lastSuccess)}`;
  } catch (error) {
    document.body.classList.add("is-stale");
    const since = state.lastSuccess ? `마지막 성공 ${clockTime(state.lastSuccess)} 기준 데이터` : "아직 받은 데이터 없음";
    $("stale-banner").textContent = `연결 끊김 — ${since}`;
    $("stale-banner").hidden = false;
    $("refresh-state").textContent = "연결 재시도 중…";
  }
}

for (const button of document.querySelectorAll("[data-filter]")) {
  button.addEventListener("click", () => setFilter(button.dataset.filter));
}
$("drawer-close").addEventListener("click", closeDetail);
$("drawer-backdrop").addEventListener("click", closeDetail);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeDetail();
});

refresh();
setInterval(() => {
  refresh();
  loadDetail();
}, REFRESH_MS);
