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

cleanup() { [ -n "${RUNTIME_PID:-}" ] && kill "$RUNTIME_PID" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

EXTRA=()
[ -n "${RUNTIME_LAB_REPORT:-}" ] && EXTRA+=(--lab-report "$RUNTIME_LAB_REPORT")
[ "${RUNTIME_CILIUM:-0}" = "1" ] && EXTRA+=(--cilium-policy-status)
echo "runtime Gateway: http://127.0.0.1:$RUNTIME_PORT (기록: $RUNTIME_DATA_DIR${RUNTIME_LAB_REPORT:+ · lab 보고서: $RUNTIME_LAB_REPORT})"
(cd "$ROOT/runtime" && "${RT[@]}" mvp \
  --gateway-port "$RUNTIME_PORT" --engine-port "$ENGINE_PORT" --data-dir "$RUNTIME_DATA_DIR" ${EXTRA[@]+"${EXTRA[@]}"}) &
RUNTIME_PID=$!

for _ in $(seq 1 30); do
  curl -sf "http://127.0.0.1:$RUNTIME_PORT/api/summary" >/dev/null 2>&1 && break
  sleep 1
done

if [ "${RUNTIME_DEMO:-0}" = "1" ]; then
  (cd "$ROOT/runtime" && "${RT[@]}" mvp-demo --gateway "http://127.0.0.1:$RUNTIME_PORT" --data-dir "$RUNTIME_DATA_DIR") || true
fi

# update 보고서(RPT JSON) 폴더. 기본은 저장소의 update/out(update CLI가 만든 실제 보고서).
export UPDATE_REPORTS_DIR="${UPDATE_REPORTS_DIR:-$ROOT/update/out}"
echo "update 보고서: $UPDATE_REPORTS_DIR"
echo "platform: http://127.0.0.1:${BYUL_PLATFORM_PORT:-8700}"
cd "$ROOT/platform/api"
RUNTIME_BASE_URL="http://127.0.0.1:$RUNTIME_PORT" uv run byul-platform
