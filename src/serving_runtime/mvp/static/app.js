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
  const external = status.backend_kind === "vllm";
  // The mock mode reports `mock_engine`; the external vLLM mode reports `backend`.
  const engine = status.mock_engine || status.backend || {};
  const backendBadge = $("backend-badge");
  backendBadge.textContent = external
    ? `외부 vLLM 모드 · ${engine.healthy ? "연결됨" : "연결 안 됨"} · 정식 GPU 시험(S3B) 전`
    : "모의 엔진";
  backendBadge.className = external
    ? `badge ${engine.healthy ? "badge-external" : "badge-ERROR"}`
    : "badge badge-mock";
  $("engine-title").textContent = external ? "외부 backend" : "모의 엔진";

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

  const concurrency = status.concurrency || {};
  const receipts = engine.receipts_store || {};
  setDot("engine-dot", engine.healthy);
  const engineRows = [
    ["주소", engine.url, { mono: true }],
    ["연결", engine.healthy ? "정상" : "응답 없음", { problem: !engine.healthy }],
    ["점검", formatTime(engine.checked_at)],
    ["동시 요청", `${concurrency.in_flight ?? "–"} / ${concurrency.limit ?? "–"}`],
    ["수신 기록", receipts.ok === false ? `저장 오류: ${receipts.last_error}` : receipts.state || "–", { problem: receipts.ok === false }],
  ];
  if (external) {
    engineRows.push(["자격정보", engine.credential || "–", { problem: engine.credential !== "configured" }]);
    engineRows.push(["추론 검증", engine.inference_verified ? "확인됨" : "미확인 (health는 연결 가능 여부만 뜻함)"]);
  }
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

function stateCell(value) {
  return el("span", { className: `badge state-${value}`, text: value });
}

function renderPolicyState(policy) {
  const badges = $("policy-state-badges");
  clear(badges);
  const badgeRows = [
    ["읽기", policy.loaded, policy.loaded ? "성공" : "실패"],
    ["Gateway 활성", policy.gateway_active, policy.gateway_active ? "활성" : "비활성"],
    ["외부 집행", false, "미연결"],
  ];
  for (const [label, ok, value] of badgeRows) {
    badges.appendChild(el("span", { className: `badge badge-state ${ok ? "ok" : "bad"}`, text: `${label}: ${value}` }));
  }
  const validity = $("validity-rows");
  clear(validity);
  const labels = { period: "기간", revocation: "철회", approval_ref: "승인 근거" };
  const results = { not_configured: "미설정", not_validated: "검증 안 함" };
  for (const item of policy.validity || []) {
    validity.appendChild(el("tr", {}, [
      el("td", { text: labels[item.item] || item.item }),
      el("td", { text: item.state }),
      el("td", { text: results[item.result] || item.result }),
    ]));
  }
  const history = $("history-rows");
  clear(history);
  for (const item of policy.history || []) {
    history.appendChild(el("tr", { className: item.same_version_content_changed ? "changed" : "" }, [
      el("td", { text: formatTime(item.at) }),
      el("td", { text: item.event }),
      el("td", { text: item.policy_id ?? "–" }),
      el("td", { text: item.policy_version ?? "–" }),
      el("td", { className: "mono", text: item.sha256 ? `${item.sha256.slice(0, 12)}…` : "–", title: item.sha256 }),
      el("td", { text: item.previous?.policy_version ?? "–" }),
      el("td", { text: item.same_version_content_changed ? "같은 판본 내용 변경" : "–" }),
    ]));
  }
  const store = policy.history_storage || {};
  $("history-store-state").textContent = store.ok ? "저장 정상" : `저장 오류: ${store.last_error || "확인 불가"}`;
}

function renderBaseline(baseline) {
  $("model-id-limit").textContent = baseline.model_id_limit;
  const body = $("baseline-rows");
  clear(body);
  for (const item of baseline.rows || []) {
    body.appendChild(el("tr", {}, [
      el("td", { className: "mono", text: item.field }),
      el("td", { text: item.expected }),
      el("td", { text: item.observed ?? "–" }),
      el("td", { text: item.source }),
      el("td", { text: formatTime(item.observed_at) }),
      el("td", {}, [stateCell(item.state)]),
    ]));
  }
}

function assetSummary(item) {
  // asset_integrity: collection can succeed while files differ from the approved list.
  const value = item.source === "asset_integrity" ? item.last_value : null;
  if (!value || !value.counts) return [];
  const c = value.counts;
  const problem = !value.intact;
  const text = `일치 ${c.match}/${value.total} · 변경 ${c.changed} · 누락 ${c.missing} · 읽기 불가 ${c.unreadable} · 링크 ${c.symlink} · 목록 밖 파일 ${value.extra_file_count} · 목록 밖 링크 ${value.extra_symlink_count ?? 0} · 순회 오류 ${value.walk_error_count ?? 0}`;
  return [el("div", { className: problem ? "problem" : "muted", text, title: value.limit })];
}

function renderCollectors(payload) {
  const body = $("collector-rows");
  clear(body);
  for (const item of payload.collectors || []) {
    const origin = item.data_origin === "mock" ? "모의" : item.data_origin ?? "–";
    body.appendChild(el("tr", {}, [
      el("td", { className: "mono", text: item.source }, assetSummary(item)),
      el("td", {}, [stateCell(item.state)]),
      el("td", { text: formatTime(item.last_success_at) }),
      el("td", { text: item.last_error ?? "–" }),
      el("td", {}, [el("span", { className: item.data_origin === "mock" ? "badge badge-mock" : "", text: origin })]),
    ]));
  }
}

function observationBadge(origin) {
  if (origin === "synthetic") return el("span", { className: "badge badge-synthetic", text: "합성" });
  if (origin === "real") return el("span", { className: "badge badge-ALLOWED", text: "실제" });
  return el("span", { className: "badge badge-INCOMPLETE", text: "미확인" });
}

function observationState(origins) {
  if ((origins?.real || 0) > 0) return ["실제 수집", "chip-real"];
  if ((origins?.synthetic || 0) > 0) return ["합성 데이터", "chip-synthetic"];
  return ["미연결", "chip-off"];
}

function setObservationChip(id, label, stateValue) {
  const chip = $(id);
  chip.textContent = `${label} 관측 · ${stateValue[0]}`;
  chip.className = `chip ${stateValue[1]}`;
}

function podLabel(pod) {
  if (!pod) return "–";
  const name = pod.pod || "–";
  return pod.namespace ? `${pod.namespace}/${name}` : name;
}

function renderObservations(payload) {
  const items = payload.observations || [];
  const origins = payload.origins_by_kind || {};
  setObservationChip(
    "network-observation-chip",
    "네트워크",
    observationState(origins.network_flow),
  );
  setObservationChip(
    "process-observation-chip",
    "프로세스",
    observationState(origins.process_exec),
  );
  setObservationChip(
    "file-observation-chip",
    "파일",
    observationState(origins.file_access),
  );
  setObservationChip("gpu-observation-chip", "GPU", ["미연결", "chip-off"]);

  const corrupt = payload.corrupt_lines || 0;
  $("observation-corrupt").textContent = `손상 줄 ${corrupt}`;

  const hubble = items.filter((item) => item.source === "hubble" && item.kind === "network_flow");
  const hubbleBody = $("hubble-observation-rows");
  clear(hubbleBody);
  $("hubble-observation-empty").hidden = hubble.length > 0;
  for (const item of hubble) {
    const destination = `${podLabel(item.dst)} · ${item.dst?.ip || "–"}:${item.dst?.port || "–"}/${item.dst?.protocol || "–"}`;
    hubbleBody.appendChild(el("tr", {}, [
      el("td", { text: formatTime(item.observed_at) }),
      el("td", { text: podLabel(item.src) }),
      el("td", { className: "mono", text: destination }),
      el("td", { text: item.verdict || "–", title: item.reason || "" }),
      el("td", {}, [observationBadge(item.data_origin)]),
    ]));
  }

  const tetragon = items.filter((item) => item.source === "tetragon");
  const tetragonBody = $("tetragon-observation-rows");
  clear(tetragonBody);
  $("tetragon-observation-empty").hidden = tetragon.length > 0;
  for (const item of tetragon) {
    const outsideAllowlist = item.kind === "process_exec" && item.allowed_binary === false;
    tetragonBody.appendChild(el("tr", { className: outsideAllowlist ? "exec-outside-allowlist" : "" }, [
      el("td", { text: formatTime(item.observed_at) }),
      el("td", { text: item.kind === "process_exec" ? "프로세스" : `파일 ${item.access || "other"}` }),
      el("td", { className: "mono", text: item.path || item.binary || "–", title: podLabel(item.pod) }),
      el("td", { className: "mono", text: `${item.rule_id || "–"} · ${item.action || "–"}` }),
      el("td", {}, [observationBadge(item.data_origin)]),
    ]));
  }
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
    if (item.stream) {
      row.children[2].appendChild(el("span", { className: "badge badge-previous", text: "stream" }));
    }
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

function contextText(context) {
  if (!context) return "설정 안 됨";
  const labels = { within_limit: "한도 이내", exceeded: "한도 초과(미전달)", unavailable: "토큰 수 확인 불가(미전달)" };
  const tokens = context.input_tokens === null || context.input_tokens === undefined ? "–" : context.input_tokens;
  return `${labels[context.result] || context.result} · 입력 ${tokens}토큰 / 한도 ${context.limit}`;
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
        ["HTTP 시작 상태", final.http_start_status],
        ["최종 결과", final.final_outcome, { mono: true }],
        ["스트림", final.stream === true ? "예" : final.stream === false ? "아니요" : null],
        ["사유 코드", decision.reason_code, { mono: true }],
        ["설명", decision.message],
        ["적용 규칙", decision.rule_id, { mono: true }],
        ["모델", final.model],
        ["max_tokens", final.max_tokens],
        ["context 검사", contextText((finalRecord.request || {}).context)],
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
        [
          "backend 취소",
          final.backend_cancel === "mock_engine_recorded"
            ? "모의 엔진 기록 (실제 엔진 취소 확인 아님)"
            : final.backend_cancel,
        ],
      ]),
    ]),
  );

  const timeline = el("ol", { className: "timeline" });
  for (const record of detail.gateway_records) {
    timeline.appendChild(
      el("li", {}, [
        el("div", {
          className: "phase",
          text: record.phase === "started"
            ? "전달 전 시작 기록"
            : record.phase === "response_started"
              ? "HTTP 200 응답 시작"
              : "최종 기록",
        }),
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
    const [status, summary, list, policy, baseline, collectors, observations] = await Promise.all([
      getJson("/api/status"),
      getJson("/api/summary"),
      getJson(`/api/requests${query}`),
      getJson("/api/policy"),
      getJson("/api/baseline"),
      getJson("/api/collectors"),
      getJson("/api/observations"),
    ]);
    renderStatus(status);
    renderSummary(summary);
    renderRequests(list.requests);
    renderPolicyState(policy);
    renderBaseline(baseline);
    renderCollectors(collectors);
    renderObservations(observations);
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
