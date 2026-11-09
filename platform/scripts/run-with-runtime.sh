#!/usr/bin/env bash
# runtime Gateway(모의 엔진)와 byul platform을 함께 띄운다. 화면은 platform 하나다(runtime·update 모두).
#
#   platform/scripts/run-with-runtime.sh                  # runtime :8080, platform :8700
#   RUNTIME_DEMO=1 platform/scripts/run-with-runtime.sh   # runtime 시연 요청(mvp-demo)과 저장소의 합성 관측 자료도 넣는다
#
# 기존 시험 기록 연결(있을 때 자동):
#   RUNTIME_DATA_DIR   runtime 기록 폴더. 기본: runtime 기본 위치(~/.local/state/serving-runtime/mvp)가 있으면 그것, 없으면 platform/.runtime-data
#   RUNTIME_LAB_REPORT kind lab 시험 보고서(파일 또는 폴더). 기본: ~/.local/state/serving-runtime/k8s-lab/reports 가 있으면 그것
#   RUNTIME_CILIUM=1   Cilium 정책 상태 조회(kind-runtime-lab context 필요)
#   RUNTIME_VIEWER_TOKEN  runtime 조회 보호를 켰다면 platform이 서버 쪽에서 보낸다
#   BYUL_ACCOUNTS_FILE    로그인 계정 파일(0600). 없으면 임시 폴더에 admin/admin을 만든다(종료 시 삭제)
#   BYUL_LOGIN_DISABLED=1 로그인 끄기(개발용, 이전 동작)
#   EVIDENCE_INDEX_PATHS  오프라인 생성 증거 index 절대 경로(:로 여러 개). 생략하면 예시 증거
#   RUNTIME_TETRAGON_STATE=1  명시한 context의 Tetragon 상태를 읽기 전용으로 수집
#   RUNTIME_KUBE_CONTEXT     기본 kind-runtime-lab
#   TETRAGON_EVIDENCE_DIR    Tetragon 시험·모드 전환 기록 폴더
#   RUNTIME_TETRAGON_IDENTITY_SELECTOR  대상 Pod selector(설정하면 식별 자료 수집)
#   RUNTIME_TETRAGON_NAMESPACE          기본 serving-lab
#   RUNTIME_TETRAGON_STREAM_POLICIES    gRPC로 수집할 정책 이름, 쉼표 구분
#   RUNTIME_TETRAGON_BASELINE           사전에 확인한 정책 spec·모드 기대값 파일
#   RUNTIME_TETRAGON_MONITOR_WINDOW=1   Gateway 실제 관측으로 모니터 창 생성(opt-in)
#   RUNTIME_TETRAGON_MONITOR_POLICIES   대상 정책, 쉼표 구분(기본 runtime-tg-secret-01)
#   RUNTIME_TETRAGON_MONITOR_REVIEWS    사람이 작성한 이벤트별 검토 JSON(선택)
#   RUNTIME_TETRAGON_MONITOR_OUTPUT     에이전트 --review-file 출력(기본 data-dir/tetragon-monitor-windows.json)
#
# runtime은 byul/runtime의 코드를 그대로 쓴다(uv 임시 환경에 편집 모드로 설치, runtime/ 폴더에는 파일을 만들지 않음).
# platform은 runtime 조회 API(GET /api/*)만 읽는다.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
RUNTIME_PORT="${RUNTIME_PORT:-8080}"
ENGINE_PORT="${ENGINE_PORT:-8088}"
STATE_HOME="${XDG_STATE_HOME:-$HOME/.local/state}/serving-runtime"
if [ -z "${RUNTIME_DATA_DIR:-}" ]; then
  if [ -d "$STATE_HOME/mvp" ]; then RUNTIME_DATA_DIR="$STATE_HOME/mvp"; else RUNTIME_DATA_DIR="$ROOT/platform/.runtime-data"; fi
fi
if [ -z "${RUNTIME_LAB_REPORT:-}" ] && [ -d "$STATE_HOME/k8s-lab/reports" ]; then
  RUNTIME_LAB_REPORT="$STATE_HOME/k8s-lab/reports"
fi
export PATH="$HOME/.local/bin:$PATH"
RT=(uv run -q --no-project --python 3.12 --with-editable . python -m serving_runtime)

if [ ! -f "$ROOT/platform/web/dist/index.html" ]; then
  echo "화면 빌드: platform/web (npm install && npm run build)"
  (cd "$ROOT/platform/web" && npm install --no-audit --no-fund && npm run build)
fi

if [ "${RUNTIME_DEMO:-0}" = "1" ] && [ ! -f "$RUNTIME_DATA_DIR/observations.jsonl" ]; then
  # 저장소에 있는 합성 관측 자료(runtime/tests/fixtures)를 runtime 정규화 경로로 넣는다. 실제 관측이 아니다(합성 표시).
  echo "합성 관측 자료 넣기: runtime/tests/fixtures/enforcement"
  (cd "$ROOT/runtime" && \
    "${RT[@]}" enforce ingest --kind hubble --input tests/fixtures/enforcement/hubble_flows.synthetic.jsonl \
      --origin synthetic --data-dir "$RUNTIME_DATA_DIR" --environment kind-runtime-lab --intent deploy/lab/intent.lab-v1.1.json && \
    "${RT[@]}" enforce ingest --kind tetragon --input tests/fixtures/enforcement/tetragon_events.synthetic.jsonl \
      --origin synthetic --data-dir "$RUNTIME_DATA_DIR" --environment kind-runtime-lab --intent deploy/lab/intent.lab-v1.1.json) || true
fi

if [ "${RUNTIME_DEMO:-0}" = "1" ]; then
  # 기존 관측 폴더에도 시연용 Override를 한 번만 보탠다. 성공 후에만 표지를 남긴다.
  mkdir -p "$RUNTIME_DATA_DIR"
  DEMO_OVERRIDE_MARKER="$RUNTIME_DATA_DIR/.tetragon-override-synthetic-v1.ingested"
  if [ ! -f "$DEMO_OVERRIDE_MARKER" ]; then
    echo "합성 Tetragon Override 관측 자료 넣기"
    if (cd "$ROOT/runtime" && "${RT[@]}" enforce ingest --kind tetragon \
      --input tests/fixtures/enforcement/tetragon_override.synthetic.jsonl \
      --origin synthetic --data-dir "$RUNTIME_DATA_DIR" --environment kind-runtime-lab \
      --intent deploy/lab/intent.lab-v1.1.json); then
      touch "$DEMO_OVERRIDE_MARKER"
    fi
  fi
fi

cleanup() {
  [ -n "${TETRAGON_MONITOR_PID:-}" ] && kill "$TETRAGON_MONITOR_PID" 2>/dev/null || true
  [ -n "${TETRAGON_IDENTITY_PID:-}" ] && kill "$TETRAGON_IDENTITY_PID" 2>/dev/null || true
  [ -n "${TETRAGON_STREAM_PID:-}" ] && kill "$TETRAGON_STREAM_PID" 2>/dev/null || true
  [ -n "${TETRAGON_PID:-}" ] && kill "$TETRAGON_PID" 2>/dev/null || true
  [ -n "${RUNTIME_PID:-}" ] && kill "$RUNTIME_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

EXTRA=()
[ -n "${RUNTIME_LAB_REPORT:-}" ] && EXTRA+=(--lab-report "$RUNTIME_LAB_REPORT")
[ "${RUNTIME_CILIUM:-0}" = "1" ] && EXTRA+=(--cilium-policy-status)
EXTRA+=(--kube-context "${RUNTIME_KUBE_CONTEXT:-kind-runtime-lab}")
TETRAGON_FEED_DIR="${RUNTIME_TETRAGON_FEED_DIR:-$STATE_HOME/tetragon-live}"
if [ -n "${RUNTIME_TETRAGON_IDENTITY_SELECTOR:-}" ]; then
  RUNTIME_TETRAGON_IDENTITIES="${RUNTIME_TETRAGON_IDENTITIES:-$RUNTIME_DATA_DIR/tetragon-identities.json}"
fi
[ -n "${RUNTIME_TETRAGON_IDENTITIES:-}" ] && EXTRA+=(--tetragon-identities "$RUNTIME_TETRAGON_IDENTITIES")
if [ -n "${RUNTIME_TETRAGON_STREAM_POLICIES:-}" ]; then
  EXTRA+=(--observation-feed "tetragon=$TETRAGON_FEED_DIR/tetragon.jsonl"
    --observation-heartbeat "tetragon=$TETRAGON_FEED_DIR/tetragon.heartbeat.json"
    --observation-origin real --observation-environment "${RUNTIME_KUBE_CONTEXT:-kind-runtime-lab}")
fi
echo "runtime Gateway: http://127.0.0.1:$RUNTIME_PORT (기록: $RUNTIME_DATA_DIR${RUNTIME_LAB_REPORT:+ · lab 보고서: $RUNTIME_LAB_REPORT})"
(cd "$ROOT/runtime" && "${RT[@]}" mvp \
  --gateway-port "$RUNTIME_PORT" --engine-port "$ENGINE_PORT" --data-dir "$RUNTIME_DATA_DIR" ${EXTRA[@]+"${EXTRA[@]}"}) &
RUNTIME_PID=$!

for _ in $(seq 1 30); do
  curl -sf "http://127.0.0.1:$RUNTIME_PORT/api/summary" >/dev/null 2>&1 && break
  sleep 1
done

if [ -n "${RUNTIME_TETRAGON_IDENTITY_SELECTOR:-}" ]; then
  (cd "$ROOT/runtime" && uv run -q --no-project --python 3.12 --with-editable . python -B -m tools.tetragon_identities \
    --context "${RUNTIME_KUBE_CONTEXT:-kind-runtime-lab}" --namespace "${RUNTIME_TETRAGON_NAMESPACE:-serving-lab}" \
    --selector "$RUNTIME_TETRAGON_IDENTITY_SELECTOR" --output "$RUNTIME_TETRAGON_IDENTITIES" --watch) &
  TETRAGON_IDENTITY_PID=$!
fi
if [ -n "${RUNTIME_TETRAGON_STREAM_POLICIES:-}" ]; then
  STREAM_ARGS=()
  IFS=',' read -r -a STREAM_POLICIES <<< "$RUNTIME_TETRAGON_STREAM_POLICIES"
  for policy in "${STREAM_POLICIES[@]}"; do STREAM_ARGS+=(--policy "$policy"); done
  (cd "$ROOT/runtime" && uv run -q --no-project --python 3.12 --with-editable . python -B -m tools.tetragon_stream \
    --context "${RUNTIME_KUBE_CONTEXT:-kind-runtime-lab}" --namespace "${RUNTIME_TETRAGON_NAMESPACE:-serving-lab}" \
    "${STREAM_ARGS[@]}" --output "$TETRAGON_FEED_DIR") &
  TETRAGON_STREAM_PID=$!
fi
if [ "${RUNTIME_TETRAGON_STATE:-0}" = "1" ]; then
  RECOVERY_ARGS=()
  if [ -n "${RUNTIME_TETRAGON_BASELINE:-}" ]; then
    RECOVERY_ARGS+=(--expected-policies "$RUNTIME_TETRAGON_BASELINE"
      --identities "${RUNTIME_TETRAGON_IDENTITIES:?복구 확인에는 대상 식별 자료가 필요합니다}"
      --feed-heartbeat "$TETRAGON_FEED_DIR/tetragon.heartbeat.json" --observations "$RUNTIME_DATA_DIR/observations.jsonl")
  fi
  (cd "$ROOT/runtime" && uv run -q --no-project --python 3.12 --with-editable . python -B -m tools.tetragon_status \
    --context "${RUNTIME_KUBE_CONTEXT:-kind-runtime-lab}" \
    --output "$RUNTIME_DATA_DIR/tetragon-state.json" \
    --evidence-dir "${TETRAGON_EVIDENCE_DIR:-$STATE_HOME/tetragon-evidence}" "${RECOVERY_ARGS[@]}" --watch) &
  TETRAGON_PID=$!
fi

if [ "${RUNTIME_TETRAGON_MONITOR_WINDOW:-0}" = "1" ]; then
  MONITOR_ARGS=()
  IFS=',' read -r -a MONITOR_POLICIES <<< "${RUNTIME_TETRAGON_MONITOR_POLICIES:-runtime-tg-secret-01}"
  for policy in "${MONITOR_POLICIES[@]}"; do MONITOR_ARGS+=(--policy "$policy"); done
  [ -n "${RUNTIME_TETRAGON_MONITOR_REVIEWS:-}" ] && MONITOR_ARGS+=(--reviews "$RUNTIME_TETRAGON_MONITOR_REVIEWS")
  (cd "$ROOT/runtime" && uv run -q --no-project --python 3.12 --with-editable . python -B -m tools.tetragon_monitor_window \
    --context "${RUNTIME_KUBE_CONTEXT:-kind-runtime-lab}" --namespace "${RUNTIME_TETRAGON_NAMESPACE:-serving-lab}" \
    "${MONITOR_ARGS[@]}" --status "$RUNTIME_DATA_DIR/tetragon-state.json" \
    --observations "$RUNTIME_DATA_DIR/observations.jsonl" \
    --output "${RUNTIME_TETRAGON_MONITOR_OUTPUT:-$RUNTIME_DATA_DIR/tetragon-monitor-windows.json}" --watch) &
  TETRAGON_MONITOR_PID=$!
fi

if [ "${RUNTIME_DEMO:-0}" = "1" ]; then
  (cd "$ROOT/runtime" && "${RT[@]}" mvp-demo --gateway "http://127.0.0.1:$RUNTIME_PORT" --data-dir "$RUNTIME_DATA_DIR") || true
fi

# update 보고서(RPT JSON) 폴더. 기본은 저장소의 update/out(update CLI가 만든 실제 보고서).
export UPDATE_REPORTS_DIR="${UPDATE_REPORTS_DIR:-$ROOT/update/out}"
export EVIDENCE_INDEX_PATHS="${EVIDENCE_INDEX_PATHS:-}"
echo "update 보고서: $UPDATE_REPORTS_DIR"
echo "platform: http://127.0.0.1:${BYUL_PLATFORM_PORT:-8700}"
cd "$ROOT/platform/api"
RUNTIME_BASE_URL="http://127.0.0.1:$RUNTIME_PORT" uv run byul-platform
