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

function scopeBadges(environment, runId) {
  const wrap = el("div", { className: "scope-badges" });
  wrap.appendChild(el("span", {
    className: `badge ${environment ? "badge-state" : "badge-INCOMPLETE"}`,
    text: environment || "환경 미기록",
    title: environment || "과거 기록 또는 옵션 미설정",
  }));
  wrap.appendChild(el("span", {
    className: `badge ${runId ? "badge-state" : "badge-INCOMPLETE"}`,
    text: runId || "실행 미기록",
    title: runId || "과거 기록 또는 옵션 미설정",
  }));
  return wrap;
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

const TOKEN_KEY = "serving-runtime-read-token";

class AuthError extends Error {
  constructor(status, code) {
    super(`HTTP ${status} ${code}`);
    this.status = status;
    this.code = code;
  }
}

function readToken() {
  try {
    return sessionStorage.getItem(TOKEN_KEY) || "";
  } catch (_) {
    return "";
  }
}

function authHeaders() {
  // The token lives only in this tab's session storage and is sent as a header.
  const token = readToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function authFetch(url) {
  const response = await fetch(url, { cache: "no-store", headers: authHeaders() });
  if (response.status === 401 || response.status === 403 || response.status === 503) {
    let code = "";
    try {
      code = ((await response.clone().json()).error || {}).code || "";
    } catch (_) {
      code = "";
    }
    if (response.status !== 503 || code === "AUTH_UNAVAILABLE") throw new AuthError(response.status, code);
  }
  return response;
}

async function getJson(url) {
  const response = await authFetch(url);
  if (!response.ok) throw new Error(`${url} → HTTP ${response.status}`);
  return response.json();
}

function showAuthForm(error) {
  const messages = {
    AUTH_REQUIRED: "조회 API에 인증이 필요합니다. 조회 역할 토큰을 입력하세요.",
    AUTH_INVALID: "토큰이 맞지 않습니다. 다시 입력하세요.",
    ROLE_NOT_ALLOWED: "이 토큰은 조회 역할이 없습니다.",
    AUTH_UNAVAILABLE: "Gateway 인증 설정 오류(503) — 설정 파일을 확인하세요. 조회도 막혀 있습니다.",
  };
  $("auth-message").textContent = messages[error.code] || `조회 거부 (${error.message})`;
  $("auth-form").hidden = false;
}

function renderStatus(status) {
  $("session-id").textContent = `관측 세션 ${status.observation_session_id.slice(0, 8)} · ${status.environment || "환경 미기록"} · ${status.run_id || "실행 미기록"}`;
  const external = Boolean(status.backend_kind) && status.backend_kind !== "mock";
  const engineName = { vllm: "vLLM", sglang: "SGLang" }[status.backend_kind] || status.backend_kind;
  // The mock mode reports `mock_engine`; the external vLLM mode reports `backend`.
  const engine = status.mock_engine || status.backend || {};
  const backendBadge = $("backend-badge");
  backendBadge.textContent = external
    ? `외부 ${engineName} 모드 · ${engine.healthy ? "연결됨" : "연결 안 됨"} · 정식 GPU 시험(S3B) 전`
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
    ["프로필", policy.test_only ? `${policy.profile} (시험 전용)` : policy.profile || "default", { problem: policy.test_only }],
    ["대상", policy.target_id],
    ["SHA-256", policy.sha256 ? `${policy.sha256.slice(0, 12)}…` : null, { mono: true, title: policy.sha256 }],
    ["로딩", formatTime(policy.loaded_at)],
    ["유효", policy.valid ? "유효" : "무효 — 추론 요청은 503", { problem: !policy.valid }],
  ];
  if (!policy.valid) {
    policyRows.push(["사유", `${policy.error_code || ""} ${policy.error_message || ""}`.trim(), { problem: true }]);
  }
  const auth = status.auth || {};
  const authLabels = {
    not_configured: "미적용 (누구나 추론 요청 가능)",
    ok: `적용 · 주체 ${auth.principal_count ?? 0}명 · 보호 경로 ${(auth.protected_routes || []).length}개`,
    error: `설정 오류 — 추론 요청은 503 (${auth.error_code || "unknown"})`,
  };
  policyRows.push([
    "API 인증",
    authLabels[auth.state] || auth.state || "–",
    { problem: auth.state === "error" || auth.state === "not_configured" },
  ]);
  fillKv("policy-kv", policyRows);
  renderProfileWarning(policy);

  const concurrency = status.concurrency || {};
  const connections = status.connections || {};
  const receipts = engine.receipts_store || {};
  setDot("engine-dot", engine.healthy);
  const engineRows = [
    ["주소", engine.url, { mono: true }],
    ["연결", engine.healthy ? "정상" : "응답 없음", { problem: !engine.healthy }],
    ["점검", formatTime(engine.checked_at)],
    ["동시 요청", `${concurrency.in_flight ?? "–"} / ${concurrency.limit ?? "–"}`],
    ["클라이언트 연결", `${connections.active ?? "–"} / ${connections.limit ?? "–"} · 거절 ${connections.rejected ?? 0}`],
    ["요청 읽기 timeout", `헤더 ${connections.request_header_timeout_s ?? "–"}초 · 본문 ${connections.request_body_timeout_s ?? "–"}초`],
    ["수신 기록", receipts.ok === false ? `저장 오류: ${receipts.last_error}` : receipts.state || "–", { problem: receipts.ok === false }],
  ];
  if (external) {
    engineRows.push(["자격정보", engine.credential || "–", { problem: engine.credential !== "configured" }]);
    engineRows.push(["metrics 자격정보", engine.metrics_credential || "미설정", { problem: ["missing", "invalid", "insecure_file"].includes(engine.metrics_credential) }]);
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

function renderTarget(target) {
  const registration = (target && target.registration) || { state: "unavailable" };
  const labels = {
    not_configured: "미등록 — 대상 확인 없이 동작",
    ok: `등록됨 · ${registration.target_id}${registration.target_ref ? ` · ${registration.target_ref}` : ""}`,
    error: `등록 파일 오류 (${registration.error_code || "unknown"}) — 추론 요청 보류`,
    unavailable: "이 Gateway는 대상 등록을 지원하지 않음",
  };
  $("target-state").textContent = labels[registration.state] || registration.state;
  const hold = target && target.gateway_hold;
  $("target-hold").hidden = !hold;
  $("target-hold").textContent = hold ? `신규 추론 요청 보류(503): ${hold.reason_code} — ${hold.message}` : "";
  const body = $("target-rows");
  clear(body);
  for (const row of (target && target.comparison) || []) {
    body.appendChild(el("tr", {}, [
      el("td", { className: "mono", text: row.field }),
      el("td", { text: row.expected ?? "–" }),
      el("td", { text: row.observed ?? "–", title: row.note || "" }),
      el("td", { text: row.source }),
      el("td", {}, [stateCell(row.state)]),
    ]));
  }
  const instance = (target && target.instance) || {};
  const current = instance.current;
  let text = "";
  if (instance.state === "not_configured" || !target) {
    text = "Pod 조회 미설정 — 관측 인스턴스를 구분하지 않음";
  } else if (current) {
    text = `현재 인스턴스 ${current.instance_id} · ${current.pod} · 재시작 ${current.restart_count ?? "–"}회 · 조회 ${formatTime(instance.observed_at)}`;
  } else {
    text = `인스턴스 확인 불가: ${instance.state} — 추정하지 않음`;
  }
  const history = (target && target.history) || [];
  if (history.length) {
    const last = history[0];
    text += ` · 최근 이력 ${last.event}${last.reason ? `(${last.reason})` : ""} ${formatTime(last.recorded_at)}`;
  }
  $("target-instance").textContent = text;
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

function collectorSummary(item) {
  const rows = assetSummary(item);
  const value = item.last_value;
  if (item.source.startsWith("observation_feed_") && value) {
    rows.push(el("div", {
      className: value.rejected > 0 || value.corrupt_lines_total > 0 || value.out_of_window_total > 0 ? "problem" : "muted",
      text: `최근 처리 ${value.processed} · 저장 ${value.accepted} · 중복 ${value.duplicates} · 거부 ${value.rejected} · 손상 누적 ${value.corrupt_lines_total} · 실행 전 이벤트 누적 ${value.out_of_window_total || 0}`,
      title: value.path,
    }));
    if ((value.rejected_details || []).length) {
      rows.push(el("div", {
        className: "problem",
        text: value.rejected_details.map((detail) => `#${detail.line}: ${detail.reason}`).join(" · "),
      }));
    }
    const writer = value.writer || {};
    rows.push(el("div", {
      className: writer.configured ? "muted" : "problem",
      text: writer.configured
        ? `writer ${writer.state} · heartbeat ${formatTime(writer.updated_at)}${value.idle ? " · 새 이벤트 없음" : ""}`
        : "writer heartbeat 미설정 · 이벤트가 없으면 생존 여부를 확인할 수 없음",
    }));
    if (writer.last_gap) {
      const gap = writer.last_gap;
      rows.push(el("div", {
        className: "problem",
        text: `재연결 ${writer.reconnect_count ?? 0}회 · 최근 끊긴 구간 ${formatTime(gap.opened_at)} ~ ${formatTime(gap.closed_at)} · 그 사이 이벤트 복구 요청함(완전성 미확인)`,
      }));
    }
  }
  if (item.source === "record_retention" && value) {
    const files = Object.values(value.files || {});
    const removable = files.reduce((sum, file) => sum + (file.removable_lines || 0), 0);
    const gib = (bytes) => `${((bytes || 0) / 1024 ** 3).toFixed(2)}GiB`;
    rows.push(el("div", {
      className: value.over_size_limit || removable > 0 ? "problem" : "muted",
      text: `${value.mode === "enforce" ? "삭제 적용" : "보고만(삭제 안 함)"} · ${value.limits?.max_age_days}일/${gib(value.limits?.max_bytes)} · 현재 ${gib(value.total_bytes)} · 한도 초과 대상 ${removable}줄`,
    }));
    if (value.holds?.state === "error") {
      rows.push(el("div", { className: "problem", text: `보존 목록 오류: ${value.holds.error} — 삭제하지 않음` }));
    }
    if ((value.errors || []).length) {
      rows.push(el("div", {
        className: "problem",
        text: `정리 오류: ${value.errors.join(" · ")}${value.retention_log?.state === "error" ? ` (정리 로그 기록 실패: ${value.retention_log.error})` : ""}`,
      }));
    }
    const last = value.last_deletion;
    if (last) {
      const ranges = Object.entries(last.files || {}).map(([name, file]) => {
        const range = file.removable_range || {};
        const durability = file.durability === "uncertain" ? " · 교체됨, 디렉터리 fsync 실패로 내구성 불확실" : "";
        return `${name} ${file.removed_lines}줄 (${formatTime(range.from)}~${formatTime(range.to)})${durability}`;
      });
      rows.push(el("div", {
        className: "problem",
        text: `최근 정리 ${formatTime(last.ran_at)}: ${ranges.join(" · ")} — 이 구간 기록은 조회되지 않음`,
      }));
    }
  }
  if (item.source === "metrics" && value) {
    const stateLabels = { ok: "정상", type_mismatch: "형식 불일치", unsupported_or_absent: "미제공" };
    for (const [name, field] of Object.entries(value)) {
      if (!field || typeof field !== "object" || !field.state) continue;
      const reset = field.counter_reset || {};
      const parts = [
        `${name}: ${stateLabels[field.state] || field.state}`,
        `선언 형식 ${field.declared_type || "없음"}`,
      ];
      if (reset.detected_this_poll) parts.push("counter 감소 감지(엔진 재시작 가능성)");
      else if (reset.last_detected) parts.push(`최근 counter 감소 ${formatTime(reset.last_detected.detected_at)}`);
      rows.push(el("div", {
        className: field.state === "ok" && !reset.detected_this_poll ? "muted" : "problem",
        text: parts.join(" · "),
        title: field.help || field.metric || "",
      }));
    }
  }
  if (item.source === "cilium_policy" && value) {
    rows.push(el("div", {
      className: "muted",
      text: `정책 객체 ${value.object_count ?? 0}개 · 객체 존재는 실제 차단 증거가 아님`,
    }));
  }
  return rows;
}

function renderCollectors(payload) {
  const body = $("collector-rows");
  clear(body);
  for (const item of payload.collectors || []) {
    const origin = item.data_origin === "mock" ? "모의" : item.data_origin ?? "–";
    body.appendChild(el("tr", {}, [
      el("td", { className: "mono", text: item.source }, collectorSummary(item)),
      el("td", {}, [stateCell(item.state)]),
      el("td", { text: formatTime(item.last_success_at) }),
      el("td", { text: item.last_error ?? "–" }),
      el("td", {}, [el("span", { className: item.data_origin === "mock" ? "badge badge-mock" : "", text: origin })]),
    ]));
  }
}

function renderRevalidation(payload) {
  const items = payload.requests || [];
  const body = $("revalidation-rows");
  clear(body);
  $("revalidation-empty").hidden = items.length > 0;
  const triggerLabels = {
    asset_changed: "자산 변경",
    config_drift: "구성 drift",
    policy_content_changed: "정책 내용 변경",
  };
  for (const item of items) {
    const originLabel = item.data_origin === "mock" ? "모의" : item.data_origin ?? "–";
    const originClass = item.data_origin === "mock" ? "badge badge-mock" : "badge badge-state";
    const conditionLabels = {
      current: "있음",
      cleared: "해소됨",
      superseded: "다른 근거로 대체됨, 해소 아님",
    };
    const conditionClass = item.condition_state === "superseded"
      ? "badge-INCOMPLETE"
      : item.current_evidence_present ? "state-drift" : "state-match";
    body.appendChild(el("tr", {}, [
      el("td", { text: triggerLabels[item.trigger] || item.trigger }),
      el("td", { text: item.evidence_summary }),
      el("td", { text: formatTime(item.opened_at) }),
      el("td", {}, [el("span", {
        className: `badge ${conditionClass}`,
        text: item.condition_state_label || conditionLabels[item.condition_state] || "–",
      })]),
      el("td", {}, [el("span", { className: originClass, text: originLabel })]),
    ]));
  }
  const warning = $("revalidation-warning");
  warning.textContent = `열린 재검증 요청 ${payload.open_count || 0}건 · 현재 근거 ${payload.current_condition_count || 0}건 — 자동 승인·해제되지 않습니다.`;
  warning.hidden = (payload.open_count || 0) === 0;
  const corrupt = (payload.corrupt_lines || []).length;
  const errors = (payload.evaluation_errors || []).length;
  $("revalidation-state").textContent = `열림 ${payload.open_count || 0} · 현재 근거 ${payload.current_condition_count || 0} · 손상 줄 ${corrupt} · 평가 오류 ${errors}`;
}

function observationBadge(origin) {
  if (origin === "synthetic") return el("span", { className: "badge badge-synthetic", text: "합성" });
  if (origin === "real") return el("span", { className: "badge badge-ALLOWED", text: "실제" });
  return el("span", { className: "badge badge-INCOMPLETE", text: "미확인" });
}

function observationState(origins, collector) {
  const value = collector?.last_value || {};
  const writer = value.writer || {};
  if (collector && collector.state !== "ok") {
    if (writer.configured === false) {
      return [`관측 불가 (writer heartbeat 미설정 · ${collector.state})`, "chip-off"];
    }
    if ((collector.last_error || "").includes("heartbeat") || (collector.last_error || "").includes("writer")) {
      return [`관측 불가 (writer heartbeat 오류)`, "chip-off"];
    }
    return [`관측 불가 (${collector.state})`, "chip-off"];
  }
  if ((value.rejected || 0) > 0 && value.out_of_window === value.rejected && writer.configured) {
    return ["수집 정상 · 실행 전 이벤트 제외", "chip-real"];
  }
  if ((value.rejected || 0) > 0 && (value.accepted || 0) === 0) {
    return ["관측 불가 (손상 줄)", "chip-off"];
  }
  if ((value.rejected || 0) > 0) return ["실시간 수집 · 일부 제외", "chip-synthetic"];
  if (collector?.state === "ok" && writer.configured && value.idle) {
    return ["수집 정상 · 새 이벤트 없음", "chip-real"];
  }
  if (collector?.state === "ok" && writer.configured) return ["실제 수집(실시간)", "chip-real"];
  if (collector?.state === "ok") return ["실시간 기록 · writer 생존 미확인", "chip-off"];
  if ((origins?.real || 0) > 0) return ["실제 기록 · 실시간 미연결", "chip-off"];
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

function renderObservations(payload, collectorsPayload) {
  const items = payload.observations || [];
  const origins = payload.origins_by_kind || {};
  const collectorMap = Object.fromEntries((collectorsPayload.collectors || []).map((item) => [item.source, item]));
  setObservationChip(
    "network-observation-chip",
    "네트워크",
    observationState(origins.network_flow, collectorMap.observation_feed_hubble),
  );
  setObservationChip(
    "process-observation-chip",
    "프로세스",
    observationState(origins.process_exec, collectorMap.observation_feed_tetragon),
  );
  setObservationChip(
    "file-observation-chip",
    "파일",
    observationState(origins.file_access, collectorMap.observation_feed_tetragon),
  );
  setObservationChip("gpu-observation-chip", "GPU", ["미연결", "chip-off"]);

  const corrupt = payload.corrupt_lines || 0;
  $("observation-corrupt").textContent = `손상 줄 ${corrupt}`;

  const hubble = (payload.hubble_observations || items)
    .filter((item) => item.source === "hubble" && item.kind === "network_flow");
  const hubbleBody = $("hubble-observation-rows");
  clear(hubbleBody);
  $("hubble-observation-empty").hidden = hubble.length > 0;
  for (const item of hubble) {
    const destination = `${podLabel(item.dst)} · ${item.dst?.ip || "–"}:${item.dst?.port || "–"}/${item.dst?.protocol || "–"}`;
    hubbleBody.appendChild(el("tr", { className: item.verdict === "dropped" ? "observation-dropped" : "" }, [
      el("td", { text: formatTime(item.observed_at) }),
      el("td", {}, [scopeBadges(item.environment, item.run_id)]),
      el("td", { text: podLabel(item.src) }),
      el("td", { className: "mono", text: destination }),
      el("td", { text: item.verdict || "–", title: item.reason || "" }),
      el("td", {}, [observationBadge(item.data_origin)]),
    ]));
  }

  const tetragon = items.filter((item) => item.source === "tetragon");
  const sourceCounts = payload.counts?.source || {};
  $("observation-summary").textContent =
    `Hubble ${sourceCounts.hubble || 0}건 · Tetragon ${sourceCounts.tetragon || 0}건`;
  const tetragonBody = $("tetragon-observation-rows");
  clear(tetragonBody);
  $("tetragon-observation-empty").hidden = tetragon.length > 0;
  for (const item of tetragon) {
    const flaggedVerdicts = ["outside_allowlist", "suspect_binary", "exec_from_cache", "phase_unknown"];
    const outsideAllowlist = item.kind === "process_exec"
      && (item.exec_verdict ? flaggedVerdicts.includes(item.exec_verdict) : item.allowed_binary === false);
    const podUnknown = item.pod_attribution === "unavailable";
    const processUnknown = item.process_attribution === "unavailable"
      || (item.kind === "file_access" && !item.binary);
    const verdictLabels = {
      allowed_in_phase: "단계 허용",
      exception: `승인 예외 ${item.exception_id || ""}`.trim(),
      outside_allowlist: "목록 밖",
      suspect_binary: "의심 실행",
      exec_from_cache: "캐시에서 실행",
      phase_unknown: "단계 불명",
    };
    const generationLabels = { current: "현재 캐시 세대", other: "다른 캐시 세대", outside: "캐시 밖" };
    let kindLabel = item.kind === "process_exec" ? "프로세스" : `파일 ${item.access || "other"}`;
    if (item.exec_verdict) {
      kindLabel += ` · ${(item.phase || []).join("+")} · ${verdictLabels[item.exec_verdict] || item.exec_verdict}`;
    }
    if (item.cache_generation) {
      kindLabel += ` · ${generationLabels[item.cache_generation] || item.cache_generation}`;
    }
    const attributionLabels = [];
    if (podUnknown) attributionLabels.push("pod 미확인");
    if (processUnknown) attributionLabels.push("프로세스 미확인");
    const attributionLabel = attributionLabels.join(" · ");
    const rawAction = String(item.action_raw || item.action || "").toUpperCase();
    const normalizedActionMode = item.action_mode
      || (rawAction === "KPROBE_ACTION_OVERRIDE" ? "enforce" : rawAction === "KPROBE_ACTION_POST" || rawAction === "OBSERVED" ? "monitor" : "unknown");
    const actionMode = normalizedActionMode === "enforce"
      ? "차단 시도 기록(enforce)"
      : normalizedActionMode === "monitor"
        ? "감시만(monitor)"
        : "알 수 없음";
    tetragonBody.appendChild(el("tr", { className: outsideAllowlist ? "exec-outside-allowlist" : "" }, [
      el("td", { text: formatTime(item.observed_at) }),
      el("td", {}, [scopeBadges(item.environment, item.run_id)]),
      el("td", { text: attributionLabel ? `${kindLabel} · ${attributionLabel}` : kindLabel }),
      el("td", { className: "mono", text: item.path || item.binary || "–", title: attributionLabel || podLabel(item.pod) }),
      el("td", { className: "mono", text: `${item.rule_id || "–"} · ${actionMode}`, title: item.action_raw || item.action || "원본 action 미기록" }),
      el("td", {}, [observationBadge(item.data_origin)]),
    ]));
  }
}

function renderCilium(collectorsPayload, labPayload, observations) {
  const collector = (collectorsPayload.collectors || []).find((item) => item.source === "cilium_policy");
  const body = $("cilium-policy-rows");
  clear(body);
  const value = collector?.last_value;
  const policies = value?.policies || [];
  $("cilium-policy-empty").hidden = policies.length > 0;
  $("cilium-policy-empty").textContent = collector
    ? (collector.last_error ? `정책 조회 실패: ${collector.last_error}` : "CiliumNetworkPolicy 객체가 없습니다.")
    : "Cilium 정책 상태 수집원이 설정되지 않았습니다.";
  $("cilium-policy-state").textContent = collector
    ? `${collector.state} · 마지막 조회 ${formatTime(collector.last_attempt_at)} · 최근 성공 ${formatTime(collector.last_success_at)}`
    : "미설정";
  if (collector?.last_error) $("cilium-policy-state").textContent += ` · ${collector.last_error}`;
  for (const policy of policies) {
    const conditions = (policy.conditions || []).map((condition) =>
      `${condition.type || "조건"}=${condition.status || "–"}${condition.reason ? ` (${condition.reason})` : ""}`
    ).join(" · ") || "status/조건 없음";
    const nodeStatus = policy.node_status || {};
    const objectState = (nodeStatus.total || 0) > 0
      ? `node ${nodeStatus.total} · enforcing ${nodeStatus.enforcing || 0} · 오류 ${nodeStatus.errors || 0}`
      : policy.status_present ? "status 있음 · node 상태 없음" : "객체만 확인";
    body.appendChild(el("tr", {}, [
      el("td", { className: "mono", text: policy.name }),
      el("td", { text: objectState, title: nodeStatus.last_updated || "" }),
      el("td", { text: conditions }),
    ]));
  }
  const verification = $("cilium-verification");
  clear(verification);
  const configured = collector?.state === "ok" && (value?.object_count || 0) > 0;
  const l02 = labPayload.report?.cases?.find((item) => item.id === "L-02");
  const realDrops = observations.verification?.real_hubble_dropped || 0;
  const blocked = l02?.status === "passed" || realDrops > 0;
  verification.appendChild(el("span", {
    className: `badge badge-state ${configured ? "ok" : "bad"}`,
    text: configured ? `설정됨 (정책 객체 ${value.object_count}개)` : "정책 객체 확인 불가",
  }));
  verification.appendChild(el("span", {
    className: `badge badge-state ${blocked ? "ok" : "bad"}`,
    text: blocked
      ? `저장된 실제 차단 근거 있음 (${l02?.status === "passed" ? "L-02 통과" : ""}${l02?.status === "passed" && realDrops ? " · " : ""}${realDrops ? `Hubble DROPPED ${realDrops}건` : ""})`
      : "실제 차단 확인 안 됨",
  }));
}

function renderLabReport(payload) {
  const report = payload.report;
  $("lab-report-state").textContent = payload.state === "ok"
    ? `${report.overall} · ${report.run_id}`
    : `${payload.state}${payload.error ? ` · ${payload.error}` : ""}`;
  const body = $("lab-report-rows");
  clear(body);
  $("lab-report-empty").hidden = Boolean(report);
  $("lab-report-empty").textContent = payload.state === "not_configured"
    ? "lab 보고서가 설정되지 않았습니다."
    : payload.state === "missing"
      ? "lab 보고서 파일을 찾지 못했습니다."
      : `lab 보고서를 읽지 못했습니다: ${payload.error || "알 수 없는 오류"}`;
  fillKv("lab-report-meta", report ? [
    ["실행", report.run_id, { mono: true }],
    ["환경", `${report.context} · ${report.namespace}`],
    ["완료", formatTime(report.finished_at)],
    ["전체", report.overall],
  ] : [["상태", payload.state], ["경로", payload.configured_path || "미설정", { mono: true }]]);
  for (const item of report?.cases || []) {
    const evidence = (item.evidence || []).join(" · ");
    body.appendChild(el("tr", {}, [
      el("td", { className: "mono", text: `${item.id} · ${item.title}` }),
      el("td", {}, [stateCell(item.status)]),
      el("td", { text: `${item.summary}${evidence ? ` · 증거: ${evidence}` : ""}`, title: item.limitation || item.note || "" }),
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
      el("td", {}, [scopeBadges(item.environment, item.run_id)]),
      el("td", { text: item.model ?? "–" }),
      el("td", { className: "num", text: item.max_tokens ?? "–" }),
      el("td", {}, [badge(item.result)]),
      el("td", { className: "mono muted", text: item.reason_code ?? "–" }),
    ]);
    if (item.stream) {
      row.children[3].appendChild(el("span", { className: "badge badge-previous", text: "stream" }));
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

function renderProfileWarning(policy) {
  const warning = $("profile-warning");
  if (!policy || !policy.test_only) {
    warning.hidden = true;
    return;
  }
  const passthrough = (policy.extra_fields || [])
    .filter((item) => item.mode === "passthrough")
    .map((item) => item.name);
  warning.textContent =
    `시험 전용 프로필(${policy.profile}): 검사하지 않고 엔진에 전달하는 필드 ${policy.passthrough_count ?? passthrough.length}개`
    + `${passthrough.length ? ` (${passthrough.join(", ")})` : ""}`
    + ` · 검사 후 전달 ${policy.validate_count ?? 0}개 · 허용 n 최대 ${policy.max_n ?? 1}. 승인된 운영 정책이 아닙니다.`;
  warning.hidden = false;
}

function authText(auth) {
  if (!auth) return null;
  const labels = {
    not_configured: "인증 미적용",
    not_evaluated: "인증 확인 전 종료",
    authenticated: "인증됨",
    missing: "인증 정보 없음",
    invalid: "인증 실패",
    unavailable: "인증 설정 오류",
  };
  const label = labels[auth.state] || auth.state;
  if (!auth.principal_id) return label;
  return `${label} · ${auth.principal_id} (${(auth.roles || []).join(", ") || "역할 없음"})`;
}

function extraFieldsText(items) {
  if (!Array.isArray(items) || !items.length) return null;
  const modes = { validate: "검사", passthrough: "미검사 전달" };
  return items.map((item) => `${item.name}(${modes[item.mode] || item.mode})`).join(", ");
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
  body.appendChild(scopeBadges(final.environment, final.run_id));

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
        ["n", (finalRecord.request || {}).n],
        ["추가 필드", extraFieldsText((finalRecord.request || {}).extra_fields)],
        ["요청 주체", authText(finalRecord.auth)],
        ["context 검사", contextText((finalRecord.request || {}).context)],
      ]),
    ]),
  );
  body.appendChild(
    section("적용 정책", [
      kvList([
        ["정책", `${policy.policy_id ?? "–"} · 판본 ${policy.policy_version ?? "–"}`],
        ["프로필", policy.profile === "zeroday-lab" ? "zeroday-lab (시험 전용)" : policy.profile, { problem: policy.profile === "zeroday-lab" }],
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
  download.addEventListener("click", async (event) => {
    // A plain link cannot carry the Authorization header, so fetch the file.
    if (!readToken()) return;
    event.preventDefault();
    try {
      const response = await authFetch(download.href);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const url = URL.createObjectURL(await response.blob());
      const link = el("a", {});
      link.href = url;
      link.setAttribute("download", `evidence-${detail.request_id}.json`);
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) {
      if (error instanceof AuthError) showAuthForm(error);
    }
  });
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
    const [status, summary, list, policy, baseline, collectors, revalidation, observations, labReport] = await Promise.all([
      getJson("/api/status"),
      getJson("/api/summary"),
      getJson(`/api/requests${query}`),
      getJson("/api/policy"),
      getJson("/api/baseline"),
      getJson("/api/collectors"),
      getJson("/api/revalidation"),
      getJson("/api/observations"),
      getJson("/api/lab-report"),
    ]);
    // Optional: a gateway started before target support has no /api/target.
    const target = await getJson("/api/target").catch(() => null);
    renderStatus(status);
    renderSummary(summary);
    renderRequests(list.requests);
    renderPolicyState(policy);
    renderBaseline(baseline);
    renderTarget(target);
    renderCollectors(collectors);
    renderRevalidation(revalidation);
    renderObservations(observations, collectors);
    renderLabReport(labReport);
    renderCilium(collectors, labReport, observations);
    state.lastSuccess = new Date();
    $("auth-form").hidden = true;
    document.body.classList.remove("is-stale");
    $("stale-banner").hidden = true;
    $("refresh-state").textContent = `3초마다 갱신 · 마지막 ${clockTime(state.lastSuccess)}`;
  } catch (error) {
    if (error instanceof AuthError) showAuthForm(error);
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
const observationsPanel = $("observations-panel");
try {
  observationsPanel.open = localStorage.getItem("observations-panel-open") === "1";
} catch (_) {
  // Storage can be unavailable; the panel stays collapsed.
}
observationsPanel.addEventListener("toggle", () => {
  try {
    localStorage.setItem("observations-panel-open", observationsPanel.open ? "1" : "0");
  } catch (_) {
    // Ignore storage failures; the toggle still works for this page view.
  }
});
function applyToken() {
  try {
    sessionStorage.setItem(TOKEN_KEY, $("auth-token").value.trim());
  } catch (_) {
    // Without session storage the token cannot be kept; requests stay unauthenticated.
  }
  $("auth-token").value = "";
  refresh();
}
$("auth-apply").addEventListener("click", applyToken);
$("auth-token").addEventListener("keydown", (event) => {
  if (event.key === "Enter") applyToken();
});
$("auth-clear").addEventListener("click", () => {
  try {
    sessionStorage.removeItem(TOKEN_KEY);
  } catch (_) {
    // Nothing stored.
  }
  refresh();
});
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
