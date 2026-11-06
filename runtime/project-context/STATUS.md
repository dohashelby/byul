# 런타임 작업 상태

최종 갱신: 2026-11-06

- 2026-11-06 `tetragon_trial`에 기본 kind-docker 유지·local-sudo(호스트 uname -n과 Pod nodeName 일치 필수), context/image/pull-policy와 일반화 허용 플래그(구 이름 호환)를 추가했다. `tetragon_status`는 같은 context의 원본 보고서 바이트 해시를 붙여 safety evidence에 연결한다. 가짜 runner 신규 8건·관련 52건 통과, kind 기본 산출물/보고서는 e5a8ba7과 바이트 동일 확인. 지정 전체 818건(702 통과·115 skip·기존 소켓 PermissionError 1), `git diff --check` 통과, 로그 `/tmp/byul-tetragon-trial-tests.log`. B-7 AWS 명령·evidence-dir 연결을 기록했다. 실제 클러스터/sudo/AWS 실측은 미실행이며 단일 노드·호스트명 일치·sudo 권한·Python 이미지 준비가 필요하다. 기준 byul_runtime e5a8ba7, 기존 미커밋 변경 보존·runtime 안 5개 파일만 변경(새 tests/test_tetragon_trial.py 포함), contracts/update 수정·커밋·push 없음.
- 2026-11-06 모니터 기록 생성기 구현: 새 `tools/tetragon_monitor_window.py`는 Gateway의 real/confirmed 관측 저장소와 fresh/healthy Tetragon 상태를 읽어 정책·namespace·node별 windows 및 사람 검토용 이벤트 catalog를 0600 임시 파일→rename으로 갱신한다. 기본 `--watch` 5초, 정책 ID/spec/method/kernel·agent epoch·공백/복구/모드 이력·저장소 교체 시 새 창이며 과거/재기동 기간은 소급하지 않는다. 별도 사람 검토 파일의 verdict/검토자 ID만 집계한다. 에이전트 `--review-file` 직접 연결, 열린 fd 기준 root/실행 UID·0600 검사, namespace/ID/monitor 모드 대조, agent-state→API→운영/Tetragon 화면의 창 시작·건수·검토/오탐 표시, 개발 스크립트 opt-in을 추가했다. 설계 §6.1·사용 §11~12에 사용법·검토 절차·제약을 기록했다. 기준 `byul_runtime` HEAD `e5a8ba7` (= main, PR #227 머지됨), 커밋·push 없음, contracts/update 수정 없음. 작업 중 감지된 별도 GPU 확인순서 문서 변경과 기존 미추적 인계 파일은 보존한다.
- 2026-11-06 kind 실측(사용자 전달 Claude 기록, 분·초 시각 미제공): 6개 안전 조건 통과(`capability_basis=live_trial`) → operator 요청 requested → 자기 승인 HTTP 403 → 별도 approver 승인 → `agent --execute` set-mode monitor→enforce 통과(9초) → 시험용 Pod 보호 파일 읽기 exit 137(Sigkill) → monitor 복귀 요청 즉시 approved → 실행 후 monitor·읽기 정상. 근거 없는 요청은 플랫폼 요청 단계 SAFETY_REJECTED. 당시 임시 스크립트가 windows를 3초마다 만들었으며 새 생성기의 실측은 아니다. 이번 세션의 kind/GPU/실서버 재실행은 없고, 새 생성기 연결 후 kind 전체 경로 확인은 남아 있다.
- 생성기 검증: runtime 지정 전체 810건(694 통과·115 skip·기존 `test_gateway_serves_snapshot_without_kubernetes_mutation` 소켓 생성 PermissionError 1), 관련 42건 전부 통과(새 시험 13건). web 타입 검사·117건 통과. API 지정 uv 명령은 기본 캐시 읽기 전용으로 시작 실패; `/tmp` 캐시·offline만으로는 TestClient 진행 중단, 기존 문서 §7의 selector 보조 설정을 추가해 전체 234 통과·1 skip·기존 `test_saved_confirmed_export_starts_temporary_gateway` 소켓 생성 PermissionError 1, 관련 운영 API 38 통과·소켓 통합 1 skip. 새 API 시험의 UTC 표기 문자열 비교는 수정 후 통과했다. `bash -n`·`git diff --check` 통과. 로그 `/tmp/byul-monitor-*`, 상세 명령·제약은 사용 문서 §13. 관측 64 MiB/상태·검토 2 MiB 제한, 재기동 시 새 창, 사람 검토의 로컬 신뢰·대용량 증분 처리 미지원은 남는다.

- 2026-11-06 원격 enforce의 capability 판정 1건을 보정했다. `available`은 static, `unknown`은 evidence 검사와 같은 동일 노드·커널·방식·정책 spec의 real combined passed 근거로 live_trial 통과, `unavailable`은 근거가 있어도 우선 거부한다. 준비 결과의 basis/거부 사유를 API가 보존하고 화면에 표시하며, API·웹은 같은 6개 체크를 요구한다. 판정표·불일치·kind 상태 파일 구조 회귀시험과 설계 §6/사용/계약후보 문서를 갱신했다. 기준 `byul_runtime` HEAD `b712bd5` 위 기존 미커밋 운영 구현 보존, 이번 새 파일·contracts/update 변경·커밋·push 없음. 실제 kind/AWS 전환은 미실행이다.
- 이번 검증: runtime 지정 전체 797건(통과 681·skip 115·기존 소켓 생성 PermissionError 1), 관련 `tests.test_operations_agent` 29건 통과. web 타입 검사 통과; 기본 병렬 시험 시간 초과·화면 대기 실패 11건 뒤 `npm test -- --maxWorkers=1` 전체 115건 통과. API 지정 uv 명령은 기본 캐시 읽기 전용으로 시작 실패; 기존 문서의 `/tmp` 캐시·오프라인·selector 보조 설정으로 전체 233건 통과·skip 1·기존 소켓 생성 PermissionError 1, 관련 `tests/test_operations.py` 37건 통과·소켓 통합 1건 skip. 로그 `/tmp/byul-capability-*.log`. `git diff --check` 통과.

- 2026-11-06 dry-run 검증 통과를 `checked/DRY_RUN`('실행 없이 확인 완료')으로 종결하도록 에이전트·플랫폼 결과 모델/검증·화면 상태 필터/진행 표시·운영 문서 2개를 수정했다. 검증 실패는 `failed`와 해당 사유를 유지하며 종결 요청의 재승인·재임대·실행 보고 거부를 회귀시험으로 확인했다. 기준 `byul_runtime` HEAD `b712bd5` 위 기존 미커밋 운영 구현 보존, 새 파일·contracts/update 변경·커밋·push 없음.
- 이번 검증: runtime `TMPDIR=/dev/shm .venv/bin/python -B -m unittest discover -s tests` 793건 중 소켓 권한 오류 1·skip 115, 관련 `tests.test_operations_agent` 25건 통과. web 타입 검사 통과; 기본 `npm test` 시간 초과 4건 뒤 `npm test -- --maxWorkers=1` 전체 110건 통과. API 지정 uv 명령은 기본 캐시 읽기 전용으로 시작 실패; 기존 문서의 `/tmp` 캐시·오프라인·selector 보조 설정으로 `tests/test_operations.py` 30건 통과·소켓 통합 1건 skip. 로그 `/tmp/byul-checked-*.log`. `git diff --check` 통과. 실제 kind/서버 재검증은 미실행이다.

- 최신 작업: 운영 Tetragon 이벤트의 Pod·컨테이너·프로세스 귀속과 감시 중단·복구 확인을 구현했다. 두 Pod/주·자식·스레드 접근과 에이전트 실제 교체·살아 있는 수집기의 전달 중단을 로컬 kind에서 확인했다. 개발 화면은 실제 8개 정책과 복구 조건/공백 이력을 조회한다. 기준 `82804c1`, 사용자 요청으로 이번 변경을 로컬 커밋한다. 상세·검증 범위는 아래 최신 기록 및 `docs/Tetragon_운영귀속_복구_20261106.md`.

- 현재 후속 결과: 시험 수집의 Pod 귀속 누락을 커널 task·CRI 대조로 보완한 뒤 Sigkill·LSM Override+Sigkill 각각 새 namespace 3회 연속 통과, Override 단독 EPERM 진단 통과, syscall 미지원 재확인. 실제 MODE·기능 조건·전환 기록·근거 다운로드 화면 연결. runtime 742건(skip 0)·API 197건·web 92건 및 타입 검사 통과. 사용자 요청은 로컬 커밋까지만이며 AWS/GPU 적용·PR·푸시·머지는 범위 밖이다. 일반 운영 이벤트의 WSL 귀속 제약과 Tetragon 에이전트 버그는 남는다. 상세는 아래 2026-11-06 후속 검증 기록.

- 2026-11-06 사용자 요청으로 정책 묶음 내보내기·Tetragon 후보 PR #224를 main에 머지했다(`1a03e6c`, GitHub 필수 검사 7개 성공). `byul_runtime`도 같은 커밋으로 동기화했다. 준비 중 CI의 runtime 패키지 메타데이터 누락과 모드 전환 시간 초과 뒤 rollback 누락 경로를 수정했다. 소켓 허용 환경의 runtime 전체 725건 전부 통과(skip 0), 깨끗한 Git 사본 API 194건, web 88건·타입 검사, CI 경로 분기 10건과 공용 계약 검사 통과. 실제 kind·AWS 신규 후보 차단 효과는 아직 미검증이다.
- 후속 작업 순서: (1) 파일 읽기 거부·프로세스 종료의 판정과 시험 보완, (2) 로컬 kind에서 시험용 Pod의 후보별 동작·복귀 실측, (3) 실제 정책 MODE·선택 방식·지원 점검·전환/복귀 결과·시험 근거를 대시보드에 연결, (4) AWS 시험용 Pod부터 두 엔진·서빙 복구 검증, (5) 인증된 서버 제어 경로로 화면 모드 전환과 묶음 자동 적용 연결. 후속 기능 구현과 서버 가동은 이번 PR 머지 작업에서 하지 않았다.

- 2026-11-05 체크리스트 MD에 AWS-01~20 대응표와 37개 행별 `기존 AWS 실증으로 충족한 범위` 칸을 추가했다. 직통 제한·context 경계·input_ids 안전 거부·Gateway 준비 경계·선정 송신 제한 등 기존 통과를 인정하고, 잔여 요구와 변경 영향 회귀만 다음 작업으로 구분했다. 기존 시험 절차/구현/미확인 칸은 보존했다. 새 시험은 실행하지 않았다.
- 2026-11-05 입력→런타임 연결: 사용자가 환경 계약의 필요한 선택 필드 추가를 승인했다. CONFIRMED 정책·환경 판본의 해시를 확인하고 runtime CLI로 묶음을 만들어 다운로드·감사 기록하는 API와 정책 화면 버튼을 구현했다. DRAFT→승인→내보내기→verify→임시 Gateway `bundle_id`/`gateway_active`/`verified`까지 자동 시험으로 확인했다. 서버 적용은 사람 작업이며 실서버 재적용은 미확인이다.
- 2026-11-05 Tetragon 차단: 명시적 정확 파일 목록과 세 방식(`override_lsm`, `sigkill_fd_install`, `override_syscall`)의 1.7.1 monitor 후보, 동일 ID MODE 전환·rollback·신규 이름 선로드 교체, 사전 능력 판별, L-05b/L-05c·AWS-20 방식별 판정, 화면 Sigkill “차단 시도” 표지를 구현했다. 4회차 값에서는 `override_lsm` 불가로 분류한다. 모든 신규 후보에 Sigkill이 들어가므로 엔진 대상 AWS-20에는 별도 opt-in을 요구한다. 실제 kind/AWS load·차단 효과와 엔진 주 프로세스 종료→503→재시작은 미확인이다. 5회차 확인순서 초안을 추가했으며 서버는 켜지 않았다.
- 검증: runtime `unittest discover -s tests` 723건 중 115건 skip·실패 0, web `npm run typecheck && npm test` 88건 통과. PR 준비에서 API 시험의 로컬 runtime 가상환경 의존을 제거하고 CI와 같은 `uv run --frozen --with-editable ../../update --with-editable ../../runtime python -m pytest -q` 깨끗한 Git 사본 194건 통과, CI 경로 분기 시험 10건 통과, 공용 계약 schemas 13·examples 11 오류 0, `git diff --check` 통과. 기준 `925ca62`, 사용자 요청으로 PR·머지 진행. 남은 개발은 차단 판정·엔진 주 프로세스 시험 보완, 현재 정책 MODE·점검 결과·시험 근거의 대시보드 연결, 이후 인증된 서버 적용 연결이다. kind와 AWS 실측은 아직 하지 않았다.

- 2026-11-05 체크리스트 37개 MD를 카테고리별 표에서 착수 순서 01~37의 단일 표로 바꿨다. HTML 질문 원문을 왼쪽에 그대로 넣고 행마다 로컬 시험·개발·GPU/운영/다중 worker 단계와 바로 할 일을 표시했다. 기존 시험 절차·구현/기존 증거·미개발/미확인/다음 작업 칸은 유지했다. 새 기능 구현·시험 실행은 하지 않았다.
- 2026-11-05 체크리스트 37개 문서의 시험 완료선에 사용자 지적을 반영했다. 로컬 시험은 사전검증이며, 실제 vLLM·SGLang 지원 주장은 현재 코드·묶음·엔진 판본의 GPU 통합 증거가 필요하다. GPU 자체와 무관한 인증/보존 등은 실제 배포 통합·운영 저장소에서 확인하고, 다중 worker 항목은 별도 환경이 필요함을 구분했다. 새 시험은 실행하지 않았다.
- 2026-11-05 사용자 제공 HTML의 `봐야 하는` 37개를 `docs/런타임_체크리스트37_시험_현황_우선순위.md`로 옮겨 시험 절차·합격 기준·기존 구현/시험 증거·미확인·개발 순서를 대응시켰다(P0 19, P1 17, P2 1). 원본 37개 ID 대조와 `git diff --check`를 확인했다. 이번 작업에서 신규 시험 실행·기능 구현·커밋·push는 하지 않았다. 기존 작업 트리 변경은 보존했다.
- 2026-11-05 AWS `bundle apply` 4회에서 이전 Pod port-forward로 자동 조회 불가였고 새 수동 포워드의 ID·`verified` 일치를 확인했다. 연결 실패 시 포워드 재개설(기본 90초), `--existing` 재검증·복귀, dry-run sudo 안내를 수정했다. 로컬 전체 시험 712건 통과(skip 115), `git diff --check` 통과; 수정판의 AWS 재적용·Pod 종료 구간 자동 확인은 미검증이다.

## 2026-11-05 플랫폼 정책 묶음 build/apply와 후보 목록 (기준 e6985d7, 미커밋)

- 검수 지적 반영: apply의 `/api/status` 조회 토큰을 서버 env 관례인 `RUNTIME_VIEWER_TOKEN`으로 맞추고, 비보호 200 응답은 토큰 없이 처리한다. rollout 뒤 새 임시 port-forward를 열어 최대 30초 재시도한다. 실제 값 불일치·rollout 실패는 이전 링크 복원·재시작, 연결·인증 등 조회 불가는 새 링크를 유지하고 수동 확인을 요구한다. `--kubeconfig`를 모든 kubectl 호출에 전달하며 sudo 실행 예시를 갱신했다. 실제 서버 재실행은 아직 없다.
- 검수 반영 후 전체 `TMPDIR=/dev/shm .venv/bin/python -B -m unittest discover -s tests`: 694건, 실패·오류 0, skip 115. `git diff --check` 통과.
- `bundle build`가 CONFIRMED `byul-policy/0.1` 원본과 독립 확인 정규 해시·서버 값·intent를 받아 새 ID 묶음을 생성하고 기존 verifier로 확인한다. `bundle apply`는 root:10000 권한으로 서버 루트에 배치·재검증, 상대 `current` 링크 교체, Gateway 재시작·`/api/status` 승인 `verified` 확인, 실패 시 링크 복원·재시작을 수행한다. `--dry-run` 제공. 변환·거부 규칙과 수동 대체 절차는 `docs/정책묶음_변환규칙.md`, `deploy/gpu-lab/정책_수동적용_절차.md`에 기록했다. 승인 목록은 자기 신고 사본이며 검증된 플랫폼 승인이 아니다.
- LAB `platform-approval.aws-lab.json`은 원본 전체가 아닌 요약본이라 원본 해시 `b37fa34…`를 재검산할 수 없다. 합성 EXAMPLE 정책 정규 해시 `e6ff3f32…`와 시험 서버 값으로 vLLM·SGLang 묶음을 만들었고 둘 다 `bundle verify` 통과, Gateway 정책 파일은 기존 LAB 바이트와 동일(`472a7cca…`). intent·digest 자리값은 시험 사본에서만 채웠다. 실제 원본·서버 권한·kubectl·HTTP·AWS 적용은 미확인이다.
- `docs/후보상태_기능목록.md`에 F6 지표, SP1 캐시, F4 SGLang drift, F7 승인 검증기, 관리 listener와 3회차의 제한된 SGLang 빈 캐시 관찰을 기록했다. `docs/지원_미지원_목록.md`의 backfill 전달 1건과 PR #216 빈 캐시 기동 1회 확인을 3회차 §3·§4 근거로 갱신했다. 다음은 플랫폼 원본 판본·실제 서버 값으로 생성 검증, 배치 권한과 되돌리기 실습, 실제 엔진의 후보 값 대조다.
- 로컬 전체 `TMPDIR=/dev/shm .venv/bin/python -B -m unittest discover -s tests`: 692건, 실패·오류 0, skip 115(소켓/환경 제한). `git diff --check` 통과. AWS 서버에는 접속·적용하지 않았고 커밋·push하지 않았다.

## 2026-11-04 AWS GPU 서버 3회차 결과 (기준 b3fc80e, Claude 실행)

- 20:21~20:54 KST 가동. 범위는 `deploy/gpu-lab/서버3회차_확인순서.md` 1~7번이고, 실행한 사례는 모두 최종 통과했다. vLLM AWS-09·10·11·13, SGLang AWS-09·10·11·13·16·17, 복귀 AWS-01·02다. 확인한 것: gap backfill 실제 전달 1건, SGLang 빈 캐시 첫 기동의 compiling이 준비 표지까지 유지, 셸 suspect 유지, 추론 `ptxas` 허용, Ready 전 요청 15건 Gateway 503·엔진 도달 0건, `ptxas` 파일·상위 폴더 UID 2000 쓰기 불가. vLLM 오경고 2건은 이전 묶음이 적용돼 있던 구간에서 난 것으로 보는 것이 유력하다. 묶음 ID 미기록으로 직접 확인은 못 했다. 결과는 `docs/AWS_GPU_서버3회차_결과_20261104.md`, 증거는 `~/.local/state/serving-runtime/aws-lab/evidence-20261104-run3.tgz`. 승인은 runtime 담당 자기 승인(LAB)이다.

## 2026-11-04 SGLang 첫 기동 단계·추론 JIT 보정 (기준 8c53741, 미커밋)

- 11/4 빈 캐시 첫 기동 1회에서 캡처 종료 15:57:27Z~준비 15:57:38Z 사이 셸 9건을 반영해 SGLang compiling을 준비 표지까지 유지한다. 표지 누락 시 캡처 뒤 15초(관찰 11초+여유 4초), 캡처도 없으면 `startupProbe`의 20분 상한에서 `unknown`으로 돌아간다. vLLM 표지는 그대로다. 추론의 Triton `ptxas`는 정확한 경로와 기존 Python 부모 조건에서만 허용하고 셸 의심 경고는 유지한다.
- 로컬 전체 `TMPDIR=/dev/shm .venv/bin/python -B -m unittest discover -s tests`: 686건 중 통과 571·skip 115·실패/오류 0. `git diff --check` 통과. AWS 서버에 다시 적용·확인하지 않았다. 새 intent를 담은 정책 묶음의 `enforcement_intent` 해시·크기 재계산, 검증, 사람의 후보 규칙·승인 검토가 필요하다. 엔진 `/models`는 읽기 전용이나 엔진 루트의 `readOnlyRootFilesystem`은 설정되지 않았다.
- 다음 서버 가동: 호스트 수집기와 Gateway 이미지를 새 `collector_state`를 읽는 같은 커밋으로 함께 갱신한다. 수집기만 갱신하면 기존 Gateway가 새 필드를 거부해 SGLang 단계와 AWS-11·16·17 판정이 `unknown`이 된다. 빈 캐시 첫 기동에서 캡처~준비 사이 셸의 compiling/부모 설명과 suspect 경고, 준비 뒤 첫 요청의 `ptxas` 경로·부모·판정, 준비 전 Service Endpoint·Gateway 요청의 엔진 미도달 여부를 확인한다. vLLM loading 단계 `ptxas`·`nvcc` 2건은 이미 허용 목록에 있는데 경고가 나온 원인이 미확인이다. 이번에는 바꾸지 않았고 다음 서버 관측 기록으로 확인한다.
- `deploy/gpu-lab/서버3회차_확인순서.md`를 작성했다(기준 `b3fc80e`, 서버 미가동).

## 2026-11-04 AWS GPU 서버 2회차 결과 (기준 byul_runtime 5737fd3, 문서 미커밋)

- 실제 서버 인수 시험에서 vLLM·SGLang 모두 AWS-01~19 최종 통과. vLLM AWS-11은 이전 gap 처리 뒤, AWS-13은 로그 재조회 뒤 통과했고 SGLang AWS-16·17·18도 판정됐다. 배포 경계 실측은 두 엔진 모두 `ok`. 상세 판정과 중간 `unknown`은 [2회차 결과](../docs/AWS_GPU_서버2회차_결과_20261104.md)에 기록했다.
- 남은 일: 서버 뒤 교차 검수 보완판 재실행과 backfill 전달 확인, 대시보드 화면 확인, SGLang 캡처 뒤 컴파일·첫 요청 Triton JIT 및 vLLM loading 컴파일의 오탐 처리. AWS-11의 경고 0건은 캐시가 있는 시험 구간의 결과다.
- `docs/지원_미지원_목록.md`를 2회차 vLLM·SGLang AWS-01~19 판정과 platform 화면 코드에 맞춰 갱신했다. 2회차 이미지·묶음 정확한 이름, PR #213 backfill 실제 전달, PR #216 단계 보정 서버 재확인은 미확인으로 남겼다(문서만 수정).

## 2026-11-04 AWS 2회차 수집 공백·AWS-13 로그 경합 보정 (기준 byul_runtime 5737fd3, 미커밋)

- Hubble·Tetragon 이전 미닫힘 gap은 가장 이른 시각부터 backfill을 요청하고 첫 정상 tick까지 관측 불가로 유지한 뒤 `collector_restart`/`requested_unverified`로 닫는다. 재단절과 손상 이력은 자동 봉합하지 않는다. 여러 gap·닫힌 이력·시간 역전 기록을 모의 시험했다.
- AWS-13 로그 조회는 매 호출 전 남은 8초를 확인하고, 증거 첫 줄의 key·token·secret·password·credential·auth 이름과 값을 가린다. JSON의 따옴표로 감싼 키와 `--api-key`·`--admin-key` 공백 옵션도 가리며, 일반 오류 문장은 유지하는 시험을 추가했다. 해시 오류 문구가 끝내 없으면 기존처럼 unknown이다. 실제 AWS 서버 재판정은 하지 않았다.
- `TMPDIR=/dev/shm .venv/bin/python -B -m unittest discover -s tests`: 샌드박스 683건 실패·오류 0, skip 115 / Claude 노트북 소켓 허용 실행 683건 OK, skip 0(가리기 재수정 후 기준). `git diff --check` 확인. contracts 수정 없음, 커밋·푸시 없음. 실제 AWS 서버 재시험이 남았다.

## 2026-11-03 stream 기한 결과 코드 회귀 보정 (기준 8d515ec, 미커밋)

- chat·completions의 stream 본문 읽기 중 공통 감시가 연결을 닫아도 최종 결과를 `STREAM_DEADLINE` / `deadline_exceeded`로 기록한다. 엔진 응답 헤더를 받기 전 기한 초과는 8d515ec의 `ENGINE_TIMEOUT`에 맞추고, 일반 응답·토큰 계산의 `ENGINE_DEADLINE`은 유지한다. `STREAM_IDLE_TIMEOUT`, `STREAM_RELAY_ERROR`, `ENGINE_TIMEOUT`, `ENGINE_UNREACHABLE`, `CLIENT_DISCONNECTED` 분기를 기준 커밋과 grep으로 대조했다.
- 소켓 없는 감시 종료 회귀시험을 chat·completions의 응답 시작 전후에 추가했다. `.venv/bin/python -B -m unittest discover -s tests`: 674건, 실패·오류 0, skip 115. `git diff --check` 통과. 로컬 소켓 통합 시험은 이 환경에서 건너뛰어졌으므로 Claude가 소켓 허용 노트북에서 전건 재시험해야 한다. contracts 수정 없음, 커밋·푸시 없음.

## 2026-11-03 엔진 초기 수집 회귀 보정 (기준 8d515ec, 미커밋)

- 엔진 health/info/metrics를 느린 작업용 `dedicated_thread`와 구분되는 엔진 그룹 한 스레드로 모았다. Gateway 시작 시 첫 수집 순회를 `status_timeout_s`(기본 5초)까지 기다린 뒤 HTTP 서비스를 시작한다. 시간이 초과되면 시작은 계속되고, 수집은 백그라운드에서 완료된다. 보안 사건·대상 Pod 일반 루프는 엔진 수집과 별도로 계속 돈다.
- 소켓 없는 CollectorManager 시험으로 시작 직후 엔진 3종 snapshot, 지연 수집의 시작 대기 상한·후속 회복, 엔진 지연 중 보안 사건·대상 Pod 수집을 확인했다. 집중 시험 15건 통과, 전체 `.venv/bin/python -B -m unittest discover -s tests` 660건 중 실패·오류 0, skip 115. HTTP 통합 시험은 로컬 소켓 제약으로 미실행이므로 Claude가 소켓 허용 노트북에서 전건 재시험해야 한다. contracts 수정 없음, 커밋·푸시 없음.

## 2026-11-03 HTTP/1.0 닫힌 응답 소켓 보정 (기준 8d515ec, 미커밋)

- 일반 응답과 chat·completions stream에서 응답 소켓의 `settimeout`이 `OSError`를 내면 연결 생성 시 지정한 제한 시간으로 읽기를 계속한다. 일반 응답은 각 `read1` 직후 전체 기한을 다시 확인한다. HTTP/1.0 `will_close` 응답의 200 전달, 두 stream, 소켓 재설정 실패 후 504 기한 초과, Gateway의 chat·completions 전달을 소켓 없는 회귀시험으로 확인했다.
- 관련 소켓 없는 집중 시험 48건 통과. 전체 `.venv/bin/python -B -m unittest discover -s tests -v`: 658건, 실패·오류 0, skip 115(로컬 소켓 제약). context·SGLang·target의 기존 소켓 없는 단위 시험은 집중 시험에 포함했다. HTTP 통합 시험은 이 환경에서 미실행이므로 소켓 허용 노트북에서 전체 재시험이 필요하다. contracts는 수정하지 않았고 커밋·푸시하지 않았다.

## 2026-11-03 엔진 수집·응답 기한·completions stream 보정 (기준 8d515ec, 미커밋)

- 엔진 health/info/metrics를 전용 수집 스레드로 분리하고 상태 조회의 `status_timeout_s` 기본 5초를 추론·토큰 계산·모의 receipt의 `relay_timeout_s`와 분리했다. 관측 feed·Cilium·대상 Pod는 일반 수집 루프를 유지한다. revalidation·대상 관측 콜백의 기존 잠금을 확인했다.
- 일반 응답에 토큰 계산부터 본문 수신까지 공통 `request_deadline_s`와 64 MiB 본문 상한을 적용했다. 기한 초과는 504 `ENGINE_DEADLINE`, 과대 본문은 502 `ENGINE_BAD_RESPONSE`로 최종 기록한다. completions SSE는 이벤트 검증 후 즉시 전달하고 중간 오류·잘림·연결 끊김·기한·유휴 초과를 최종 기록한다. contracts는 수정하지 않았다.
- 회귀시험 8건을 추가·보정했다. `.venv/bin/python -B -m unittest discover -s tests`: 655건, 실패 0·skip 115(로컬 소켓 제약). `git diff --check` 통과. 실제 GPU·서버 시험과 소켓 허용 환경의 HTTP 전건은 미확인이다. 기존 미추적 인계 문서는 건드리지 않았고 커밋·푸시하지 않았다.

## 2026-11-03 SGLang Claude 검수 보정 (기준 2f5f461, 미커밋)

- SGLang v0.5.22 원본 파서의 CLI 우선 병합과 `prepare_server_args` 내부 인자 파싱을 확인했다. 공개 엔진 옵션은 `engine.yaml` 인자로 옮기고 Secret `config.yaml`은 관리 키만 담도록 절차를 수정했다. 기존 Secret 갱신과 이전 데이터 키 제거·복구 절차를 기록했다. 11/3 저장 Tetragon의 SGLang Pod만 대조해 관찰되지 않은 `/usr/bin/python3`, `/usr/bin/python3.12`를 SGLang intent에서 제거했다.
- 캐시 재기동의 첫 표지 전 약 40초는 단계 미확인이고 첫 기동 JIT 경계는 로그 부재로 미확인이다. `engine_signal` 단계 전용 경로는 신뢰 단계 허용이 아니라 경고로 남는다. 새 Secret 읽기·기동과 `--config` 적용 뒤 `/proc` 인자 제거는 서버에서 확인해야 한다. 시작 로그와 `/server_info`의 관리 키 노출은 이미 관찰됐으므로 반출 전 값 검사가 필요하다. 다음은 서버 전환 뒤 AWS-09~11 재판정과 Claude 검수다.
- `runtime/.venv/bin/python -m unittest discover -s tests`(runtime 작업 폴더에서는 `.venv/bin/python`): 642건 OK, 115건 skip. SGLang 배포 집중 시험 2건 OK, `git diff --check` 통과. 서버 배포·실제 GPU 시험은 이번 보정에서 실행하지 않았다.

## 2026-11-03 AWS GPU preflight 검수 보정 (기준 46f0309, 미커밋)

- kind 재현 지적을 반영해 namespaced Tetragon 정책을 `tracingpoliciesnamespaced`로 조회하고, 빈 수집기 `ExecStart`를 `unknown`으로 판정한다. 증거 폴더 5 GiB 검사와 별도로 containerd 이미지 저장소 여유(기본 60 GiB, 경로·기준 옵션)를 확인하며 조회 불가는 `unknown`이다. 가짜 runner 회귀시험 3건을 추가했다.
- 서버 순서표에 SGLang digest 고정 이미지 선받기와 12단계 전 완료 확인, Gateway 태그 비교, 관리 키의 알려진 `/proc` 인자 노출 기록, SGLang OOM·GPU 메모리 확인을 반영했다. 실제 AWS·SGLang 실행은 미확인이다. `python3 -B -m unittest tests.test_aws_lab_preflight tests.test_aws_lab_acceptance`: 85건 통과, skip 0. `git diff --check` 통과. 커밋·푸시하지 않았다.

## 2026-11-03 AWS GPU 11/4 서버 전 준비 (기준 46f0309, 미커밋)

- 읽기 전용 `tools/aws_lab_preflight.py`와 가짜 runner 시험 8건, `deploy/gpu-lab/서버_실행순서표.md`를 추가했다. 확인 근거는 기존 인수 도구의 실제 `--cases`·`--allow-*` 옵션, GPU lab 수동 정책 절차, SGLang 전환 절차다. SGLang도 vLLM과 같은 승인 판본·정책 파일을 쓰도록 전환 문구를 바로잡았다.
- `python3 -B -m unittest tests.test_aws_lab_preflight tests.test_aws_lab_acceptance`: 82건 통과, skip 0. `git diff --check` 통과. 서버·실제 kubectl·실제 Gateway·SGLang 실행은 미확인이다. 다음은 서버 전 digest·승인 원본 해시를 확정하고, 11/4에 순서표대로 preflight → 두 엔진 인수 시험 → vLLM 복귀·증거 반출을 실제로 확인하는 것이다. 커밋·푸시하지 않았다.

## 2026-11-03 AWS 단계·거부 기록 판정과 보존 보정 (기준 887291e, 미커밋)

- AWS-16은 수집 중단 전에 vLLM 시간표의 15초 신선도(`ExecPolicy._collector_stale`)와 현재 엔진 Pod UID를 확인한다. SGLang은 단계 수집 꺼짐으로 `unknown`이다. AWS-17도 재시작 전 같은 신선도와 UID를 확인하고 SGLang에서는 `unknown`이다. AWS-11은 기존 SGLang 단계 판정 `None` 경로를 남은 시간표 시험으로 확인했다.
- AWS-19는 경로 기록 조회 실패·잘못된 응답을 `unknown`, 조회 성공 후 `found:false`를 기록 없음(`failed`)으로 구분한다. 한도 초과 수는 Gateway API에 노출되지 않아 기록 누락과 한도 초과를 구분할 수 없음을 증거에 적는다. 거부 기록 JSONL을 보존 대상에 넣고 `recorded_at`·`request_id` 단위와 hold 동작을 시험했다.
- `.venv/bin/python -B -m unittest tests.test_aws_lab_acceptance tests.test_mvp_retention`: 87건 통과. 전체 `discover -s tests`: 616건, 실패 0·skip 115. SGLang HTTP 통합 8건은 로컬 소켓 생성 `Operation not permitted`로 skip. `git diff --check` 통과. 실제 AWS·소켓 허용 환경 시험은 미확인이다. 다음은 AWS-11·16·17·19와 거부 기록 보존을 실제 환경에서 재확인하는 것이다. 기존 미커밋 변경을 보존했고 커밋·푸시하지 않았다.

## 2026-11-03 SGLang 1~6단계 (기준 887291e, 미커밋)

- Codex가 구현하던 중 사용 한도로 전체 시험 직전에 중단되어, 마지막 수정은 **Claude 구현분, Codex 교차 검수 전**이다: 가짜 SGLang 인수 사례의 `/ready`·`/v1/models` 전환, 존재하지 않는 `--max-model-len`을 `--context-length`로 교체(SGLang v0.5.22 `arg_groups/fields/model.py`), `kustomize` 순환으로 렌더되지 않던 overlay를 `deploy/gpu-lab-sglang/`으로 이동, 전환 절차의 복원 저장본 충돌 방지.
- `kubectl kustomize deploy/gpu-lab-sglang` 렌더 확인. 실제 SGLang 기동, 요청 ID 로그, UID 2000 실행, `--admin-api-key` 인자 노출, 내부 포트, 관리 경로 노출, 지표 이름은 **미확인**이다. 다음은 시험 준비와 서버 1회차다.

## 2026-11-03 AWS 추가 인수 시험 구현 (기준 887291e, 미커밋)

- AWS-12의 별도 비교 Pod·Cilium 정책과 규칙 전 연결/적용 중 timeout+Hubble dropped/제거 뒤 재연결 판정을 추가했다. 기본 kustomization과 serving-engine·Gateway 정책은 변경하지 않는다. 기존 AWS-01~11 번호·합격 기준을 유지하고, AWS-13~17은 명시 선택·변경 허용 플래그가 있어야 실행한다.
- AWS-13 해시 불일치 묶음 재시작 거부와 이전 상대 링크 복구, AWS-14 재시작 직후 503→10초 내 200, AWS-15 수집 중단 중 재시작과 복구, AWS-16 시간표 15초 만료와 일반 요청 200, AWS-17 Pod UID 교체와 원시 Tetragon `process.pod.uid`를 대조하는 경로를 추가했다. AWS-07·08에는 단계 상태를 보조 근거로만 기록한다. 수집 중단 중 새 Tetragon 이벤트가 Gateway에 들어오지 않으므로 AWS-16의 단계 unknown은 실제 시간표와 런타임 만료 코드로 판정한다. `process_exit`의 건너뜀/손상 집계는 AWS-17 보조 근거다.
- 로컬 가짜 runner·임시 파일 시험과 전체 unittest 583건을 실행해 실패 0건, skip 113건(로컬 소켓 생성 불가 110건, 소켓 I/O 불가 3건)을 확인했다. 실제 서버의 네트워크 규칙 효과, Gateway 묶음 거부·복구, 503 시간창, 수집 중단, GPU Pod 교체, 관측 화면은 **미확인**이다. 서버 시험 명령과 복구·정리 절차는 `deploy/gpu-lab/ACCEPTANCE.md`에 있다. 커밋·푸시하지 않았다.

## 2026-11-03 SGLang 출력 상한·범위 밖 경로 기록 (기준 887291e, 미커밋)

- 실패 시험으로 SGLang completions의 잘못된 출력 상한 필드와 범위 밖 경로의 기록 누락을 확인한 뒤, chat에는 `max_completion_tokens`, completions에는 `max_tokens`를 전달했다. 가짜 SGLang도 경로별 한 필드만 읽고 없으면 16으로 처리한다. 범위 밖 경로는 별도 `gateway_route_rejections.jsonl`에 제한된 필드로 기록하며, 한 프로세스의 한 시간 창당 1,000건 뒤에는 메모리 초과 건수만 센다. 이 상한은 반복 탐색의 디스크 쓰기·fsync 폭증을 막기 위한 것이다. 정상 요청 집계와 플랫폼 입력은 바꾸지 않았다.
- runtime 전체 unittest 576건 중 463건 통과·113건 skip(로컬 소켓 생성 110, 소켓 I/O 3), 실패 0건. 새 경로별 HTTP 통합 시험은 소켓 제한으로 skip되어 실제 Gateway 전달 경로와 실제 SGLang 실행은 미확인이다. 다음은 소켓 허용 환경에서 chat/completions 각 stream·일반 요청과 거부 기록을 재시험하는 것이다. 커밋·푸시하지 않았다.

## 2026-11-03 단계·AWS-11 결함 5건 (기준 887291e, 미커밋)

- 수정 전 실패 시험으로 교체 Pod UID의 이전 단계 부착, AWS-11의 Tetragon 공백·수집기 불가 무시, 역순 줄 뒤 열린 단계 유지, 같은 초 경계의 0초 구간, 바이트 제한 뒤 같은 시각 줄 누락을 재현했다. 이번 검수에서도 실제 Tetragon `process.pod.uid` 형식과 `--since-time` 초 단위 재조회에 대한 실패 시험을 먼저 추가했다. 수집기 시간표는 이벤트 `uid`가 `collector.pod_uid`와 일치할 때만 단계를 적용한다. UID가 없거나 다르면 `phase_unknown`; `collector` 없는 수동 선언 시간표는 기존 단계 판정을 유지한다. 역순 뒤 열린 단계는 마지막 정상 시각에 닫고 다음 시작 표지에서 회복한다. 커서가 속한 초의 줄 해시로 재조회 중복을 건너뛰고 처음 보는 역순 줄만 역순으로 센다. 바이트 제한 페이지에 새 줄이 없으면 다음 초부터 조회하며 이력 미완전을 유지한다.
- AWS-11은 시험 구간의 Tetragon gap, 수집기 불가, 관측 조회 불가 시 경고 목록 미완전·`unknown`이다. 단계 근거와 경고 0건의 기존 합격 요건은 유지한다. 로컬 runtime 전체 567건 중 455건 통과·112건 skip(소켓 생성 제한 109, 소켓 I/O 제한 3), `git diff --check` 통과. 실제 AWS 재시험과 GPU 단계 패턴·정상 허용 목록은 미확인이다. 다음은 변경 이미지를 적용한 AWS-09~11 및 Pod 교체·로그 잘림 관측 확인이다. 커밋·푸시하지 않았다.

## 현재 단계

- **2026-11-02 단계 연결 로컬 구현(서버 미적용).** 기준 `34f6395`, 기존 2단계 미커밋 변경 유지. 의심 경로 판정을 모든 Pod에 복원하고, 엔진 selector의 단계·허용·예외·캐시 범위는 유지했다. 호스트 수집기가 현재 엔진 Pod UID의 `kubectl logs --timestamps`를 재생해 시간표를 원자 교체하고 Gateway가 읽는다. 수집 실패/Pod 모호/15초 stale은 `phase_unknown`이며 요청 차단 원인이 아니다. AWS-11은 유효 단계 실행이 없으면 `unknown`이다. 허용 목록은 넓히지 않았다. 저장 로그 5개 중 2개에서 기존 표지 각 5건 일치했으나 GPU 전체 패턴은 미검증. 저장 Tetragon 재생은 exec 309건(엔진 `phase_unknown` 272, 기타 판정 없음 37). 검수 지적(샌드박스에서 skip되어 드러나지 않은 시험 오류, 잘못된 바이트·로그 8 MiB 초과 시 단계 수집 중단)을 Codex가 수정하던 중 사용 한도로 멈춰, 시험 실행과 이 기록은 Claude가 이어서 했다(마지막 수정분 **Codex 교차 검수 전**). 수집 방식은 `--since-time` 증분 조회로 바뀌었다. 샌드박스 밖 runtime 557건 통과, platform API 93건(update 시험 파일 2개 제외), web 57건·타입 검사·Kustomize·diff 검사 통과. 합성 재현: `Received request` 줄의 표지는 무시되지만, 그런 표시 없이 표지 문구가 들어간 줄은 단계 신호로 인식된다. `engine_signal`은 신뢰 입력이 아니며 실제 요청 로그 형식은 서버에서 확인한다. 실제 서버 적용·1차 단계별 실행 관찰·허용 목록 검토·2차 판정이 남아 있다.
- **2026-11-02 Claude 검수 3건 반영(로컬).** host Pod의 마지막 확인 시각을 파일 재조회 시각 대신 `collected_at`으로 맞추고, 요청 기록의 대상 판단에서 이력 파일 전체 읽기를 제거했다. Gateway 재시작 뒤 새 스냅샷 대기 중 약 10초 503 가능성과 지속 시 중단 조건을 배포 절차·안내에 추가했다. AWS는 켜지 않았고 기존 미커밋 변경은 유지했다.
- runtime 전체 unittest 539건 중 428건 통과·111건 skip, 실패 0건. platform API 관련 60건 통과(소켓 환경에서 멈춘 TestClient 4건 제외), web 관련 8건 통과. Gateway 실제 재시작과 AWS 재시험은 미확인이다.
- **2026-11-02 로컬 수정, AWS 재시험 전.** 수집기 중단 후 직전 Pod·엔진 정보가 정상이면 요청 처리와 대상 미확인·마지막 확인 시각을 표시하고, 초기 미확인·확인된 불일치·잘못된 스냅샷은 503을 유지하도록 수정했다. 정책 묶음의 `policy_ref` ID·판본 대조, AWS-07의 stale 이후 200·미확인 및 복구 후 ok 판정, platform 경고·계약·수동 적용 절차도 갱신했다. AWS는 켜지 않았고 새 동작을 AWS에 쓰기 전 Gateway 이미지 재빌드와 재시험이 필요하다. 수집 중 같은 판본 Pod 교체는 복구까지 확인할 수 없으며 Gateway 재시작 뒤에는 이전 정상 확인을 사용할 수 없다.
- 로컬 `unittest discover` 537건 성공, 110건은 샌드박스 소켓 제한으로 skip. platform API의 계약 예시·runtime 변환·읽기 전용 경계 60건 성공, web 56건·타입 검사·빌드 성공. API 전체 시험은 update overlay 의존 패키지 부재와 오프라인 캐시 제한으로 수집 실패했고, update 제외 API 시험의 TestClient 경로는 샌드박스에서 완료되지 않아 미확인이다. AWS-07 새 기준은 로컬 모의 시험만 완료했다.
- **2026-11-01 AWS GPU lab dev 기능 연결 확인(Claude 실행, Codex 교차 검수 전).** 기준 HEAD `144798c`, Gateway 이미지 `serving-gateway:rt-894dfe7`/코드 `894dfe7`, 정책 묶음 `bundle-aws-lab-dev-894dfe7`의 `gateway_policy`·`policy_approvals`·`target`·`enforcement_intent` 모두 적용. AWS g6.2xlarge, vLLM 0.31.0 이미지 digest `sha256:c1c9f6fd…67c3`에서 AWS-01~03 통과(`aws-acceptance-20261101T073229Z-9585`). **dev 1회 확인이며 정식 평가가 아니다.**
- 기본 NetworkPolicy만으로 AWS-04·05 통과, TG-CACHE-01 부재로 AWS-06 `unknown`(`…T074054Z-11030`). Tetragon monitor 규칙 3개(`runtime-tg-asset-01`·`runtime-tg-cache-01`·`runtime-tg-secret-01`) 뒤 AWS-06 통과(`/models` EROFS, `/cache` TG-CACHE-01 기록; `…T075604Z-13453`). Cilium 규칙 4개(`runtime-engine-egress`·`runtime-engine-ingress`·`runtime-gateway-egress`·`runtime-observer-0-egress`) 뒤 AWS-01·04·05 통과(`…T075648Z-13909`). 기본 정책 `engine-runtime`·`engine-management-observer`를 원본 저장 후 약 1분 제거한 상태에서도 AWS-01·04·05 통과(EAGAIN + Hubble dropped; `…T075733Z-14397`); 스크립트 경로 실수로 자동 복원에 실패해 다음 명령에서 수동 복원했고 spec·labels 원본 일치를 확인했다. 복원 후 AWS-01·04·05·06 통과(`…T075802Z-14966`). 정책 전체 제거 대조는 미실행이다.
- AWS-07·08 통과(`…T113730Z-6730`): collector stop/start 뒤 각각 약 4초에 feed error/ok, 복구 요청 200; 중지 중 약 4초의 요청 1건은 200으로 판정 제외, stale 15초 뒤 503 hold는 관측하지 않았고 정상 종료라 gap 행은 없다. rollout 뒤 Pod UID·instance 변경과 `instance_changed`/`pod_replaced` 이력, 복구 요청 200. 교체 뒤 기본 정책+runtime 규칙에서 AWS-04·05 재실행 통과(`…T114125Z-8766`); runtime 규칙만의 확인은 교체 전이다. 최종 증거 `~/.local/state/serving-runtime/aws-lab/evidence/dev-20261101-stage6`, 요청·관측 화면 복사본 `dev-20261101-dashboard-copy-final`; EC2 stopped 확인, 컴퓨트 약 1.5시간(추정).
- platform 화면은 `RUNTIME_BASE_URL`의 Gateway 요청·관측 기록을 터널/복사본 Gateway로 확인했으나 `runtime-aws-acceptance/1.0` 판정표 화면은 없다. platform 승인 정책을 Gateway 정책 묶음으로 변환·생성·전달하는 코드는 없고 이번에는 수동 구성했다. 패킷 차단은 Cilium, `/models` 쓰기 거부는 Kubernetes 읽기 전용 mount이며 runtime은 규칙 생성·확인·기록을 맡는다. 다음은 **16 vCPU 승인 대기 중인 reference 재실행**과 대시보드 AWS 판정 화면 검토다.
- 2026-10-21 개발계획서 v1.4(개정일 2026-10-21, 문서 상태: 개발 기준 설계안)가 전달되어 저장소에 반영됐다. 이는 문서 반영이며 구현·시험·팀 승인을 의미하지 않는다.
- 2026-10-19 사용자 신가영이 작성한 개발계획서 v1.0 초안이 전달됐다. 저장소 사본은 `project-context/런타임_통제_개발계획서_v1.0.md`다.
- 사용자 요청으로 직접 개발할 모듈의 언어는 Python으로 선택했다. 실제 환경·연동 계약·정책값 등 남은 결정은 별도 확인이 필요하다.
- 초안 전달은 기술 스택 확정·팀 승인·구현 완료를 의미하지 않는다.
- 현재 요청 범위는 MVP 이후 계획(스트리밍·정책/상태·vLLM v0.1, 로컬 전용 `/tmp/serving-runtime-next-plan/`)의 S0~S3A 구현과 모의·가짜 backend 시험이다. 실제 vLLM 환경 시험(S3B)·인증·외부 통제 연동은 포함하지 않는다.
- 사용자 요청을 Claude가 정리하고 Codex가 구현·수정·시험한 뒤, Claude가 대화형 세션에서 직접 검수하고 최종 보고한다. 필요한 수정·재시험은 Codex가 맡는다.

## 확인한 환경

- GitHub 저장소: https://github.com/yeong-yi/serving-runtime
- WSL 저장소 경로: `/home/bobgy/bobproject/serving-runtime`
- 2026-10-19 Codex에서 `pwd`, `git rev-parse --show-toplevel`, `uname -a`와 WSL 환경 변수를 확인했다. 작업 폴더와 Git 루트가 위 경로로 일치하며, 명령은 WSL2의 Ubuntu 24.04에서 실행된다.
- 원본 맥락 문서 위치: `/home/bobgy/bobproject/project`
- 개발계획서 v1.4 원본: `/mnt/c/Users/bobgy/Downloads/런타임_통제_개발계획서_v1.4.md`
- 개발계획서 v1.4 저장소 사본: `project-context/런타임_통제_개발계획서_v1.4.md`
- 2026-10-21 `sha256sum` 비교 결과 원본과 저장소 사본의 SHA-256은 모두 `a350b0a1746e09d2cba23ed596fe0cc14e972b2f3b7cc3b1e72f195a908dc0e2`로 일치했다.
- 개발계획서 v1.0 원본: `/mnt/c/Users/bobgy/Desktop/bob_project/output/런타임_통제_개발계획서_v1.0.md`
- 2026-10-19 `sha256sum` 재비교 결과 원본과 저장소 사본의 SHA-256은 모두 `c4961b0578e25b016a8eeb491fd8a8776778b6d5ee43a2b22f96fb7911810c9b`로 일치했다.
- origin: `https://github.com/yeong-yi/serving-runtime.git` (Git 설정으로 확인)
- 사용자가 GitHub CLI 로그인과 VS Code에서 이 저장소를 연 상태를 확인했다.
- 2026-10-19 사용자가 전달한 Claude 응답으로, 새 저장소의 `CLAUDE.md`와 `project-context/STATUS.md`를 읽고 현재 단계·사용자 역할·Claude 검수 역할을 이해했음을 확인했다. 해당 검수에서 파일은 수정하지 않았다고 보고했다.
- 지침 배치 전 저장소에는 `README.md`, `.vscode/`, `.git/`가 있었다. 런타임 구현 코드는 아직 확인되지 않았다.
- 기존 `.vscode/`는 미추적 상태였고 이번 작업에서 수정하지 않는다.

## 이번에 준비한 내용

- 루트 `AGENTS.md`, `CLAUDE.md`에 두 도구의 역할과 참고 문서 안내를 작성했다.
- `project-context/`에 원문 두 개와 런타임 참고 요구사항, 현재 상태를 배치했다.
- 앞선 준비 작업에서 `README.md`의 기존 내용을 유지하면서 당시의 개발계획서 작성 중·구현 범위 미확정 상태와 Codex 구현·수정·시험 / Claude 검수 역할을 추가했다. 해당 README 작업의 Git 기준점은 `51be68f`이며, 그 작업에서 커밋·푸시는 하지 않았다.
- 2026-10-19 사용자가 전달한 Claude 검수 결과에 따르면 이번 README 변경의 검수가 완료됐으며 필수 수정 사항은 없다. 기존 문서와 충돌이 없고, 환경 확인 기록의 근거도 충분하다는 판단을 전달받았다.
- 2026-10-19 개발계획서 v1.0 초안을 읽고 `CLAUDE.md`에 필요한 상황에서 관련 절을 참고하도록 안내를 추가했다. 이 상태 기록의 미전달 내용도 갱신했으며 계획서 원문은 수정하지 않았다. 이번 문서 연결 작업의 Git 기준점은 `1cf7bba`이며, 코드 구현·도구 설치·커밋·푸시는 하지 않았다.
- 2026-10-19 사용자가 전달한 Claude 지적을 계획서 v1.0 §1.1.1·§1.8·§1.11(v1.4 §1.1·§8·§11)과 기존 문서에 대조해 문서별 역할·충돌 처리, 이전 요구와 새 초안의 범위 차이, 초안 전달·남은 결정 상태를 정리했다. 변경 파일은 `CLAUDE.md`, `runtime.md`, `README.md`, `AGENTS.md`, 이 상태 기록이다. 이전 요구사항 본문과 계획서 원본·사본은 보존했다. Git 기준점은 `1cf7bba`이며, 새 파일·코드 생성·도구 설치·커밋·푸시는 하지 않았다.
- 2026-10-19 사용자 요청에 따라 Python 최소 프로젝트 틀을 작성했다. 새 파일은 `.gitignore`, `pyproject.toml`, `src/serving_runtime/{__init__,__main__,cli}.py`, `tests/test_cli.py`다. `README.md`에 준비·실행·시험 명령을 추가하고 `CLAUDE.md`에 Python 선택을 반영했다.
- 개발·시험 환경은 Python 3.12.3 / WSL Ubuntu 24.04다. Codex가 최소 개발 도구로 프로젝트 전용 `.venv`·pip, setuptools 빌드 백엔드, 표준 `unittest`를 선택했다. 실행 라이브러리 의존성은 없다. 다른 Python 버전·OS는 미검증이다.
- `.venv/bin/python -m pip install --disable-pip-version-check -e /home/bobgy/bobproject/serving-runtime` 설치 성공. `.venv/bin/python -B -m unittest discover -s tests -v`는 설치된 패키지의 도움말·버전·알 수 없는 옵션 거부 시험 3개 통과. `.venv/bin/python -m pip check`와 추적 파일 변경에 대한 `git diff --check`도 종료 코드 0으로 통과했다. 새 파일 6개는 별도 Python 검사로 줄 끝 공백·최종 개행과 Python 구문을 확인했다.
- Python 최소 틀 검증 당시 실행 기능은 도움말과 버전 조회뿐이었다. Gateway·센서·저장·웹 서버·공통 스키마·보안 통제 기능은 미구현이며, 엔진·실제 통제 환경 시험은 미실시다. 개발 버전 `0.0.0.dev0`는 기능 완료를 뜻하지 않는다.
- Python 최소 틀 작업의 Git 기준점은 `b765a74`다. 계획서 SHA-256은 기존 기록과 일치하며, `.vscode/`와 시스템 Python 설정은 수정하지 않았다. 당시 검수·개선 작업에서는 커밋·푸시하지 않았다. 사용자가 전달한 Claude 코드 검수 결과는 필수 수정 없음이며, 선택적 개선 4건을 실제 파일에 대조해 반영했다. 알 수 없는 시험 옵션의 이름·표현, 인계용 README 경로, AGENTS의 Python 선택, 검사 범위 기록을 정리했다. 개선 후 실행 시험 3개를 다시 통과했고, 추적 파일 공백 검사와 새 파일 6개의 구문·공백 검사도 통과했다.


- 2026-10-19 사용자 요청으로 `file-info PATH`를 추가했다(계획서 v1.0 §1.6.5(v1.4 §6.5)의 크기·해시 확인에 활용하는 독립 개발 도구). 지정한 일반 파일을 나눠 읽고 읽은 바이트 수·SHA-256을 표시한다. 파일 내용·권한을 쓰지 않으며, 공통 스키마·승인 판정·변조 방지·모델 로딩 확인 기능은 아니다.
- 없는 파일·디렉터리·읽기 실패·관측된 읽기 중 변경은 성공 결과 없이 오류로 처리한다. FIFO는 기다리지 않고 거부한다. 새 파일은 `src/serving_runtime/file_info.py`, `tests/test_file_info.py`; 수정 파일은 CLI·CLI 시험·README·이 상태 기록이다.
- `.venv/bin/python -B -m unittest discover -s tests -v`로 기존 3개와 새 시험 9개, 총 12개가 통과했다. 알려진 SHA-256·빈 파일·큰 바이너리·원문 보존·FIFO·읽기 중 변경·CLI 정상/오류 경로를 확인했다. 추적 파일 `git diff --check`와 새 Python 파일 2개의 구문·공백 검사도 통과했다. 엔진·실제 통제 환경 시험은 미실시다.
- 이번 기능의 Git 기준점은 점검 시 HEAD인 `26da771`이다. 새 실행 의존성·도구 설치·커밋·푸시는 없으며, `.vscode/`와 계획서 원문은 수정하지 않았다. 2026-10-21 Claude가 이후 변경인 `cli.py`, `file_info.py`, `test_cli.py`, `test_file_info.py`, README 관련 절을 직접 검수했으며 필수 수정과 코드 수정은 없다.
- Codex 샌드박스에서는 `/tmp` 쓰기 제한으로 기본 시험 20개가 모두 임시 디렉터리 오류였으나, `TMPDIR=/dev/shm`으로 재실행해 20개 전부 통과했다(`test_file_info` 5개, `test_cli` 7개 포함). `file-info` 수동 실행은 README·없는 경로·디렉터리에서 각각 종료 코드 0·1·1로 기대대로 동작했고 `git diff --check`도 통과했다. 엔진·실제 통제 환경 시험은 여전히 미실시다.
- 이번에 반영하지 않은 선택 사항은 README "실행·시험" 절의 `file-info` 시험 설명(README 해당 절 삭제로 더 이상 해당하지 않음), `-`로 시작하는 경로에 필요한 `--`, 문자 장치 등 특수 파일을 거부하기 전에 `open`이 일어나는 점이다.
- 2026-10-21 협업 문서의 기본 흐름을 사용자 요청 정리(Claude) → 구현·시험(Codex) → 대화형 직접 검수(Claude) → 필요 시 수정·재시험(Codex) → 최종 보고(Claude)로 통일했다. `scripts/claude_review.py`와 해당 시험은 로컬 미커밋 상태로 보존하며 자동 실행하지 않는다.
- 2026-10-21 원격 커밋 `06b7112`("Update README.md", 2026-10-19 사용자 작성)가 README의 "Python 프로젝트 틀" 절을 삭제했고 사용자 결정으로 이를 유지했다. 그래서 README에는 준비·실행·시험·`file-info` 사용법이 없다. 로컬 커밋 2개를 그 위로 rebase해 `b026fe0`(`file-info`), `f65608d`(협업 문서)로 커밋·푸시했다. `scripts/claude_review.py`, `tests/test_claude_review.py`, `.gitignore`의 `.review/`, `.vscode/`는 검수 전 또는 개인 환경 파일이라 커밋하지 않았다.
- 2026-10-21 로컬 전용 MVP 계획서(커밋 대상 제외)를 기준으로 M0+M1을 구현했다. 새 파일은 `src/serving_runtime/mvp/` 6개 모듈, `examples/mvp/policy.json`, `tests/test_mvp_{policy,validator,mock_engine,app}.py`이며 CLI에 `mvp`를 연결했다.
- Claude가 소켓 허용 환경에서 전체 43개 통과(건너뜀 0)와 serving-runtime mvp 실제 기동·/api/status 200·POST 501을 확인했다. Codex 샌드박스에서는 소켓 제한으로 HTTP 7개가 건너뛰어졌다.
- 2026-10-21 Claude 필수 지적에 따라 메시지 `role`의 문자열 형식을 집합 조회 전에 검사하고, 요청·정책의 깊은 중첩 JSON `RecursionError`를 `MALFORMED_JSON`으로 처리했다. 지정한 validator·policy 회귀시험 17개가 모두 통과했다.
- 2026-10-21 M2 Gateway 중계·서버 발급 요청 ID·Gateway/모의 엔진 JSONL 기록·전달 확인·저장/엔진 오류 처리를 구현했다. 새 파일은 `src/serving_runtime/mvp/store.py`, `tests/test_mvp_{gateway,store}.py`이며 config/app/mock_engine/CLI와 기존 MVP app 시험을 갱신했다.
- `.venv/bin/python -B -m unittest discover -s tests -v` 결과 60개 중 42개 통과, 소켓 제한으로 HTTP 18개 건너뜀이다. 기본 데이터 위치는 `$XDG_STATE_HOME/serving-runtime/mvp` 또는 `~/.local/state/serving-runtime/mvp`다. 이후 Claude가 소켓 허용 환경에서 60개 전부 통과(건너뜀 0)와 실제 기동 요청(200 중계·403 미전달·프롬프트 미저장)을 확인했다.
- 2026-10-21 M3를 시작한 Codex 작업이 사용 한도로 중단돼(`query.py` 초안·`app.py` 일부 변경, 시험 2개 실패 상태) 사용자 승인으로 이번 단계만 Claude가 직접 구현했다. 조회 API(`/api/summary`·`/api/requests`·상세·증거 내려받기), 읽기 전용 대시보드(`mvp/static/` 현황·목록·상세, textContent 렌더링, 3초 갱신·연결 끊김 표시), 정책 무효 시 1 MiB 본문 상한, 시작 시 저장 경로 점검, 엔진 수신 기록 저장 상태 표시를 추가했다. 이전 관측 세션 요청은 실시간 조회의 미발견을 미수신 증거로 쓰지 않는다.
- `.venv/bin/python -B -m unittest discover -s tests` 결과 76개 전부 통과(Claude 실행, 소켓 허용 환경). M3 코드는 Claude가 구현했으므로 Codex 교차 검수가 필요하다. 사용자가 Windows 브라우저에서 대시보드 접속을 확인했다. 사용자 요청으로 M0~M3 코드·시험과 README MVP 실행 안내를 커밋·푸시했다(로컬 전용 MVP 계획서, `scripts/`, `tests/test_claude_review.py`, `.gitignore` 변경, `.vscode/`는 제외). M4는 미완료다.
- 2026-10-21 Codex가 M3 조회·정적 라우팅·대시보드를 교차 검수했으며 경로 탈출·XSS·증거 판정의 필수 수정 사항은 없었다.
- M4로 `mvp-demo`와 `mvp-acceptance`, JSON/Markdown 보고서, 인스턴스별 관측 세션, 손상 위치·이전 실행 표시와 README 실행 안내를 구현했다.
- `.venv/bin/python -B -m unittest discover -s tests -v` 결과 85개 중 60개 통과, 로컬 소켓 제한으로 HTTP·전체 수용 시험 25개 건너뜀이다.
- Claude가 소켓 허용 환경에서 전체 85개 시험 통과(건너뜀 0), `mvp-acceptance` MVP-01~11 전부 통과(보고서 JSON/Markdown 생성), 실행 중 서버 대상 `mvp-demo` 14개 사례 전부 통과를 확인했다. 필수 지적은 없었다. 사용자 요청으로 M4까지 커밋·푸시했다. 실제 GPU 엔진·인증·네트워크/커널 통제는 MVP 범위 밖이며 미검증이다.
- S0 기준선은 HEAD `b1d3555`, 기존 미커밋 4종(`.gitignore`, `.vscode/`, `scripts/`, `tests/test_claude_review.py`), Claude 실행 unittest 85개·MVP-01~11 전부 통과다. v1.4 D-09 대비 n=1·출력 128·본문 64KiB는 적용, 동시성 2·stream은 S1 전 미적용, context 2048은 tokenizer 확인 전이라 미적용이었다.
- S1에서 SSE 증분 중계, 시작/idle/전체 3종 제한, 일반·stream 공통 동시성 2와 429, HTTP 시작/최종 결과·모의 취소 증거 기록, 조회·대시보드·demo·S1-01~06 수용 시험을 구현했다.
- Codex 샌드박스의 전체 unittest는 96개 중 66개 통과, 소켓 제한으로 HTTP·전체 수용 시험 30개를 건너뛰었다. 이후 Claude가 소켓 허용 환경에서 unittest 96개 전부 통과(건너뜀 0), `mvp-acceptance` MVP-01~11·S1-01~06 전부 통과, 직접 코드 검수 필수 지적 없음을 확인했다. 실제 vLLM 내부 취소와 context 2048은 미검증·미적용이다.
- S2에서 정책 schema `runtime-mvp/1.1`의 기간·승인 참조와 1.0 엄격 호환, 철회 스냅샷, 요청별 Gateway 유효성 판정, 재시작 보존 정책 이력, baseline/manifest와 모의 엔진 관측 비교, 단일 백그라운드 수집 스레드, 읽기 전용 API·대시보드, 과거 요청 기록 호환을 구현했다. 요청 기록에는 정책 schema 판본을 추가했고 `policy_history.jsonl`은 저장 실패가 Gateway 시작을 막지 않도록 독립 상태로 표시한다.
- 유효성에서 구현한 항목은 schema/필드·`valid_from`·`valid_until`·철회 스냅샷이다. `approval_ref`는 표시만 하며 서명 검증·승인 서비스 연동·hot reload·외부 집행 확인은 미구현이다. 관측값은 승인 기준으로 저장·승격하지 않고, 모의 응답은 `data_origin: mock`으로 표시한다. 실제 vLLM 수집은 시도하거나 입증하지 않았다.
- S2-01~06을 기존 `mvp-acceptance`에 추가했다. Codex 샌드박스에서 `.venv/bin/python -B -m unittest discover -s tests -v`는 총 104개 중 72개 통과, 로컬 소켓 제한으로 HTTP·전체 수용 시험 32개 건너뜀이다. Python 구문 검사, 대시보드 JavaScript 구문 검사(사용 가능한 node), `git diff --check`도 통과했다.
- Claude가 소켓 허용 환경에서 전체 unittest 104개와 `mvp-acceptance` MVP-01~11·S1-01~06·S2-01~06을 모두 통과(건너뜀 0)시키고 직접 검수에서 필수 지적이 없음을 확인했다. 선택 사항으로 `valid_from > valid_until` 정책도 로드되지만 모든 요청이 503으로 안전 측 동작함을 확인했다.
- S3A는 Codex가 backend 설정(`backend.py`)·고정 목적지 중계·자격정보·수집 어댑터·시험 전용 가짜 backend(`fake_backend.py`)·단위 시험·S3A-01~08 수용 사례까지 구현한 뒤 Codex 사용 한도로 중단됐다. 사용자 지시로 Claude가 이어받아 대시보드 backend 모드 배지·`gateway_only` 증거 표시, README 외부 backend 절, `docs/S3B_실제환경_시험준비.md`, 이 기록을 작성했다. 이 마무리분은 Codex 교차 검수 전이다.
- Claude가 소켓 허용 환경에서 unittest 115개 전부 통과(건너뜀 0), `mvp-acceptance` 31개 사례(MVP-01~11·S1-01~06·S2-01~06·S3A-01~08) 전부 통과를 확인했다. S3A 사례는 `fake_backend_test`이며 실제 vLLM 연결이 아니다. vLLM 경로·지표 이름은 공식 문서 latest 기준 후보이고 고정 버전 v0.31.0 응답은 미확인이다.
- S3B는 미실행이다. 2026-10-21 이 WSL에는 GPU(`nvidia-smi` 없음)·nvidia 런타임·vLLM·k8s가 없어 실제 시험을 할 수 없다(CPU 16, RAM 약 7GiB). 필요한 입력과 절차는 `docs/S3B_실제환경_시험준비.md`에 정리했다. 커밋·푸시는 하지 않았다.
- 2026-10-21 사용자 승인으로 vLLM CPU 이미지 v0.29.0(digest `sha256:66064cf6…`)과 `Qwen/Qwen2.5-0.5B-Instruct`(revision `7ae55760…`)를 받아 CPU 사전 점검을 수행했다. S3-01~06 모두 통과(보고서 `~/.local/state/serving-runtime/s3b-cpu/reports/s3b-cpu-20261021T064056Z.json`, 비밀값 미포함). 선정 환경(GPU·v0.31.0·선정 모델)과 달라 정식 S3B 통과로 세지 않는다. vLLM 기본 로그에 Gateway 요청 ID·abort 기록이 없어 backend 측 요청 단위 증거와 엔진 내부 취소는 미확인이다. 첫 float32 실행은 OOM, bfloat16으로 재실행했다.
- CPU 점검 후 사용자가 외부 vLLM 모드 대시보드가 열리지 않는다고 보고했다. 원인은 `app.js`가 `/api/status`의 `mock_engine`만 읽어 vLLM 모드(`backend` 키)에서 스크립트 오류로 화면이 연결 끊김 상태에 머문 것이다. Claude가 두 키를 모두 읽도록 고치고 vLLM 모드 엔진 카드에 자격정보·추론 검증 미확인 표시를 추가했으며 정적 회귀 시험 1개를 넣었다. 브라우저 수준의 vLLM 모드 화면 시험은 없다.
- 사용자 요청으로 README 맨 앞에 "개발 현황 한눈에"(기능별 확인 수준: 모의/가짜 backend/실제 vLLM CPU, 미구현 목록)와 "실제 vLLM(CPU)으로 실행하기"(이미지·모델 revision·실행·상시 실행·종료 명령) 절을 추가했다. 현재 unittest 116개 전부 통과. 커밋·푸시는 하지 않았다.
- 2026-10-23 직전 교차 검수의 필수 6건을 반영했다. health 기반 외부 vLLM 배지, HTTP 헤더 부적합 credential의 `invalid`/503 미전달, CPU 사전 점검의 `overall`·종료 코드 3·포트/프로세스/Docker 복구 정리와 판정 강화를 구현했다.
- CPU 사전 점검에 backend 요청 로그 필수 옵션과 S3-03 0.5초 timeout, S3-04 모델·지표 기준을 추가하고 README·S3B 준비 문서를 2026-10-23 CPU 로그 재확인 결과에 맞췄다. 정식 GPU S3B 증거는 아니다.
- `.venv/bin/python -B -m unittest discover -s tests -v`는 124개 중 85개 통과·소켓 제한 39개 건너뜀·실패 0개였고, 변경 Python `py_compile`과 도구 `--help`도 통과했다. 실행 중인 Gateway/vLLM과 컨테이너는 건드리지 않았고 커밋·푸시하지 않았다.
- 2026-10-24 credential env·파일 값에서 끝의 LF 또는 CRLF 하나만 제거한 뒤 헤더 안전성을 검사하도록 공용화했고, CPU 사전 점검 키 읽기도 같은 규칙을 사용한다. 내부 CR/LF·앞뒤 공백·단독 CR·줄바꿈 2개는 계속 거부한다. `.venv/bin/python -B -m unittest discover -s tests -v` 결과 124개 중 85개 통과·소켓 제한 39개 건너뜀·실패 0개다. 실행 중 서버·Docker는 건드리지 않았고 설치·커밋·푸시하지 않았다.
- 2026-10-24 S3-06의 backend 종류 판정을 전체 누적 기록이 아니라 이번 사전 점검이 기록한 요청 ID로 한정해, 과거 mock 기록에 의한 재실행 거짓 실패를 제거하고 단위 시험을 추가했다.
- P2 사전 작업으로 엄격 intent, Cilium 1.20.3·Tetragon 1.7.1 JSON 정책 후보, Hubble·Tetragon 이벤트 변환 CLI와 관측 API·대시보드를 추가했다. 합성 fixture 기준이며 클러스터 적용·실효 검증은 하지 않았다.
- 표준 라이브러리만 사용했다. 전체 unittest 142개 중 102개 통과·소켓 제한 40개 건너뜀·실패 0개이며, 설치·컨테이너 조작·커밋·푸시는 하지 않았다.
- 2026-10-24 Codex가 Claude 작성분을 교차 검수해 필수 6건(배지 과장, 비정상 키 노출, 미실행의 통과 집계, 실패 시 정리, 판정 기준, README 포트)을 찾고 수정했다. 수정 중 생긴 끝 줄바꿈 키 회귀와 S3-06 누적 기록 거짓 실패도 Codex가 고쳤다. 이후 vLLM을 `--enable-log-requests --enable-request-id-headers`로 띄워 강화 기준(`--require-backend-request-log --allow-engine-restart`)으로 실행한 CPU 사전 점검이 S3-01~06 전부 통과했다(보고서 `~/.local/state/serving-runtime/s3b-cpu/reports/s3b-cpu-20261023T154404Z.json`). 허용 요청 ID의 vLLM 로그 기록, 거부 요청 ID 부재, 취소 요청 `aborted` 로그를 확인했다.
- 2026-10-24 Codex 로그인 만료로 Claude가 context 사전 통제(`max_context_tokens`, backend `/tokenize`, 초과 403·확인 불가 503, 생성 미전달)와 모델 파일 무결성 점검(`assets snapshot`, `--asset-manifest`, `asset_integrity` 수집원)을 직접 구현했다. 실제 vLLM v0.29.0 CPU `/tokenize`로 36토큰 허용·2129토큰 거부를 확인했다. unittest 157개 전부 통과(Claude, 소켓 허용). 이 두 기능은 Codex 교차 검수 전이다. 커밋·푸시하지 않았다.
- 2026-10-24 Codex가 교차 검수 필수 6건을 반영했다: 자산 순회 오류·root/상위 링크·`O_NOFOLLOW` 검증, snapshot 배타 생성, 전용 자산 수집 스레드·소요 시간/종료 기준 간격, context 해석 오류 503, 대시보드 원인 수 표시다.
- `.venv/bin/python -B -m unittest discover -s tests -v`는 166개 중 116개 통과·로컬 소켓 제한 50개 건너뜀·실패 0개였다. 실행 중인 서버·Docker는 건드리지 않았고 설치·커밋·푸시하지 않았다; 소켓 HTTP 회귀시험은 Claude 환경 재실행이 필요하다.
- 2026-10-24 `mvp/app.py`의 기록·backend 조회·중계·요청 흐름·HTTP 분기를 `request_records.py`·`engine_client.py`·`relay.py`·`request_flow.py`·`http_handler.py`로 책임별 분리하고 `app.py`에는 구성·상태 조합·수명주기를 남겼다.
- 분리 모듈 `py_compile`·단독 import·관련 unittest 105개(45개 skip)와 전체 unittest 166개(50개 skip)는 실패 0개다; 소켓 HTTP 전체 실행은 Claude 환경 검수가 필요하며 설치·서버·Docker 조작·커밋·푸시는 하지 않았다.
- 2026-10-24 자산 변경·engine API 구성 drift·같은 판본 정책 내용 변경에서 `runtime-revalidation-request/1.0` 요청을 자동 기록하고, 중복/해소/재발 이벤트·조회 API/읽기 전용 대시보드/O_EXCL 내보내기를 추가했다. 자동 승인·해제·외부 전송은 없고 D-03 연동 형식은 미합의다.
- `.venv/bin/python -B -m unittest discover -s tests -v`는 177개 중 126개 통과·샌드박스 소켓 제한 51개 건너뜀·실패 0개다. 표준 라이브러리만 사용했고 서버·Docker·Kubernetes·금지 포트는 조작하지 않았으며 설치·커밋·푸시는 하지 않았다; HTTP 전건은 Claude 환경 재실행이 필요하다.
- 2026-10-24 같은 scope의 재검증 근거 변경을 해소가 아닌 `superseded`로 기록하고 현재 근거 수를 분리했으며, 전체 unittest 205개 중 154개 통과·소켓 제한 51개 건너뜀·실패 0개를 확인했다.
- 2026-10-24 Claude가 WSL2 커널 6.18.33.2의 kind v0.33.0 단일 노드(kindest/node v1.37.0), Cilium 1.20.3+Hubble, Tetragon 1.7.1에서 최종 확인했다. 보고서는 저장소 밖 `~/.local/state/serving-runtime/k8s-lab/reports/k8s-lab-20261023T202226Z-162136.{json,md}`다.
- L-01 기준선과 L-02 Cilium(Gateway·DNS 허용, 엔진 직통·외부 연결 차단), L-03 Hubble(실제 `DROPPED`를 `--origin real`로 변환)이 통과했다.
- L-04 Tetragon 감시는 자산 쓰기·비밀 읽기·허용 목록 밖 실행을 확인했지만 kprobe의 pod·process 귀속이 비어 정책·경로·접근·시각으로 판정했고, exec export는 수 초~수십 초 지연됐다.
- L-05 차단은 적용 직후 5회 `EPERM`이었다. 앞서 기록한 "약 1분 후 재허용"·"클러스터 범위 미차단"은 2026-10-24 재조사에서 둘 다 05:13 Tetragon 재시작 전(04:56 helm upgrade로 뜬 실행)에만 나온 것으로 확인됐다. 재시작 후에는 8.5시간 경과 TG-DENY-01 3/3, 클러스터 범위 실험 정책 3분간 6/6 차단, 삭제 후 쓰기 성공이다. WSL 제한(`override_return`·`fmodret_syscall` 없음)은 syscall 대상에만 해당한다. 업그레이드 직후 불일치 원인은 미확정이다(근거 `~/.local/state/serving-runtime/k8s-lab/investigation-20261024-tetragon.md`). L-06 정리는 미실행해 전체는 `partial`이며, 이는 가짜 backend를 쓴 로컬 kind 결과로 정식 GPU·Kubernetes 1.36.5 검증이 아니다.
- 2026-10-24 대시보드 관측을 환경·실행별로 구분하고, Tetragon 원본 action을 보존하면서 차단 시도(enforce)·감시(monitor)·알 수 없음으로 나눴다. 과거 scope 없는 기록은 `환경 미기록`·`실행 미기록`으로 표시한다. Gateway 요청 scope와 관측 feed scope는 Docker vLLM과 kind 관측을 섞지 않도록 별도 옵션으로 뒀다.
- lab 보고서 읽기 전용 API·카드, Hubble/Tetragon append JSONL feed 수집원(회전·잘림·체크포인트·중복 방지·손상 줄/정체 상태), kind-runtime-lab 전용 CiliumNetworkPolicy 상태 수집원과 보조 follow 스크립트를 추가했다. 현재 보고서 JSON을 읽어 `partial`, L-01~L-05 `passed`, L-06 `not_run`을 확인했지만, feed·Cilium 조회·보조 스크립트는 실행하지 않아 실제 연속 수집과 정책 조회는 미확인이다. 정책 객체 존재와 Hubble/lab의 실제 차단 근거는 별도 표시한다.
- 보안 수정으로 동시성 슬롯을 tokenizer 호출 전에 획득하고 모든 후속 경로에서 반환하게 했다. HTTP 헤더·본문 읽기 timeout, 연결 상한과 즉시 503, 불완전 본문 기록을 추가했다. backend 추론 자격정보와 선택적 metrics 자격정보를 분리해 metrics 설정이 없으면 같은 호스트에도 Authorization을 보내지 않는다. 비밀값은 상태·로그·보고서에 넣지 않는다.
- `.venv/bin/python -B -m unittest discover -s tests`는 224개 중 169개 통과·샌드박스 소켓 제한 55개 건너뜀·실패 0개다. Python 구문 검사, `node --check src/serving_runtime/mvp/static/app.js`, `git diff --check`도 통과했다. 실행 중인 서버·Docker·kind와 kubectl은 조작하지 않았고 설치·커밋·푸시하지 않았다. Git 기준점은 `ac05f80`; 소켓 HTTP 전건과 실제 feed/Cilium 동작은 Claude 환경 검수가 필요하다.
- Claude가 소켓 허용 환경에서 직전판 unittest 224개 전부 통과(skip 0)와 실제 kind의 live feed·Cilium 상태·lab 보고서 동작을 확인했다. 검수에서 느린 1바이트 전송이 recv별 timeout을 연장하는 문제, 조용한 정상 writer와 중단을 구분하지 못하는 문제, Tetragon 시작 시 과거 로그 재생, Hubble FORWARDED 누적·DROPPED 가림을 재현했다.
- 필수 지적에 따라 헤더·본문을 monotonic 절대 deadline reader로 바꾸고, 보조 follow 스크립트가 feed별 시작 시각·최근 생존 시각·자식 상태 heartbeat를 원자적으로 기록하게 했다. Gateway는 정상 heartbeat가 있으면 무이벤트를 정상으로 유지하고, 종료·만료는 관측 불가로 표시하며, heartbeat 시작 전 이벤트를 저장하지 않고 별도 집계한다. heartbeat 없는 임의 feed는 기존처럼 보수적으로 stale 처리한다.
- Tetragon follow에 `--tail=0`, Hubble follow에 실행 시각 `--since`와 기본 `--verdict DROPPED`를 적용했다. `--include-forwarded`로 전체 verdict를 선택할 수 있고, 혼합된 기존 저장 기록은 Hubble DROPPED 최대 25건을 화면에서 우선 표시한다. 기존에 저장된 과거/allowed 기록은 자동 삭제하지 않는다.
- 수정 후 `.venv/bin/python -B -m unittest discover -s tests`는 230개 중 174개 통과·샌드박스 소켓 제한 56개 건너뜀·실패 0개다. 새 1바이트 drip 실제 소켓 시험을 포함한 HTTP 전건과 실제 heartbeat follow는 Claude 환경 재검수가 필요하다. 설치·kubectl·서버·Docker 조작·커밋·푸시는 하지 않았고 Git 기준점은 `ac05f80`이다.
- 2026-10-24 직접 handler 소켓 시험에서 미수신 drip 바이트 때문에 408 뒤 RST가 날 수 있어, RST이면 `REQUEST_HEADER_TIMEOUT`·`REQUEST_BODY_TIMEOUT` transport 기록으로 deadline을 판정하게 했다. 보조 feed는 SIGTERM/SIGHUP도 안전 지점에서 기존 정리 경로로 전환해 port-forward·Hubble·Tetragon 자식을 종료하고 signal handler를 복원한다.
- 전체 unittest 231개 중 175개 통과·샌드박스 소켓 제한 56개 건너뜀·실패/ERROR 0개다. drip 단독 10회도 소켓 제한으로 모두 건너뛰어 소켓 허용 환경의 반복 확인이 남았다. 설치·kubectl·서버·Docker 조작·커밋·푸시는 하지 않았고 Git 기준점은 `ac05f80`이다.
- 2026-10-24 Claude가 소켓 허용 환경에서 전체 unittest 231개를 2회 모두 통과(skip 0), drip 시험 단독 10/10 통과를 확인했다. 실제 kind에서 live feed의 과거 로그 미유입·Hubble DROPPED만 수집·무이벤트 구간 정상 유지·writer 종료 약 20초 뒤 heartbeat 만료 표시, SIGTERM 시 자식 3개 정리, 실제 Gateway 1바이트 drip 약 2초 종료를 확인했다. 시작 직후 Hubble relay EOF로 helper가 스스로 종료된 사례가 1회 있었다(재실행 시 정상).
- 2026-10-24 Codex 사용 한도(작업 착수 직후, 변경 없음)로 Claude가 제로데이 시험용 정책 `runtime-mvp/1.2`(`profile` default/zeroday-lab, `extra_fields` validate/passthrough, `max_n`)를 직접 구현했다. 1.0·1.1은 그대로이고, 추가 필드는 이름·모드만 기록·증거·대시보드에 남는다. 예시 `examples/mvp/policy-v1.2-zeroday-lab.json`은 승인값이 아닌 시험 출발점이다. Claude 자체 검수에서 backend 전달 후 오류 기록이 추가 필드 이름을 잃는 문제를 찾아 `engine_error`에서 보존하게 고쳤다. 전체 unittest 252개 통과(skip 0, 새 시험 21개 포함, 가짜 backend 본문 도달 HTTP 시험 포함), 기존 `mvp-acceptance` 31개 전부 통과. ZD 수용 사례는 추가하지 않았다. passthrough 필드의 context 반영, 실제 vLLM에서의 필드별 동작은 미확인이다. **Claude 구현분이라 Codex 교차 검수 전**이다. 커밋·푸시하지 않았다.
- 2026-10-24 계획서 §10.3 필수 산출물로 `docs/지원_미지원_목록.md`(기능·엔진별 확인 수준: 정식/CPU vLLM/로컬 kind/모의·가짜/합성/미구현)와 `docs/외부기능_활용목록.md`(직접 개발 모듈, 외부 도구 실측 판본·digest·출처)를 작성하고 README 현황 표의 오래된 줄을 고쳤다. 라이선스는 공개 저장소 기준 값이며 LICENSE 파일과 직접 대조하지 않아 재확인 필요로 표시했다.
- 2026-10-24 SGLang 연결 준비(§9.4·D-07): `--backend sglang`, backend `kind: sglang`, 출력 상한을 SGLang 이름 `max_completion_tokens`로 전달, `/tokenize` context 통제, `sglang:*` 지표 대응, 버전은 기본 미수집(`/server_info`가 해석된 서버 인자 전체를 돌려주므로), 대시보드 엔진 이름 표시, 예시 `examples/mvp/backend-sglang.example.json`을 추가했다. 경로·필드·지표 이름은 SGLang v0.5.21 소스에서 확인했고 실제 SGLang은 실행하지 않았다. 시험 전용 가짜 SGLang(`FakeBackend(flavor="sglang")`)으로만 시험했다.
- 위 두 작업은 Codex 사용 한도(18:14까지)로 **Claude가 구현했고 Codex 교차 검수 전**이다. 전체 unittest 264개 통과(skip 0, SGLang 시험 12개 추가), `mvp-acceptance` 31개 통과. 커밋·푸시하지 않았다.
- 2026-10-24 D-08 기록 보존(`mvp/retention.py`: 원본 3종 7일/5GiB, 기본 보고만·`--retention-enforce` 시 삭제, 보존 목록 run/request ID, 요청 단위 정리, 날짜 불명 줄 보존, `retention_log.jsonl`·대시보드 정리 구간 표시, 같은 경로 JsonlStore 공유 잠금)과 §4.6 지표 대응표 보강(선언 TYPE·HELP·label 대조, `type_mismatch`, counter 감소 감지, `metrics-check` 명령)을 Claude가 구현했다. 전체 unittest 281개 통과(skip 0), `mvp-acceptance` 31개 통과, 시험 Gateway에서 `record_retention` 수집원 동작을 확인했다. 별도 프로세스의 동시 쓰기는 보호하지 않는다. **Codex 교차 검수 전**이며 커밋·푸시하지 않았다.
- 2026-10-24 Codex 교차 검수 8건을 수정했다. 비해시 policy/backend 값을 정리된 오류로 반환하고, gateway·receipt를 파일 간 request 묶음으로 보존하며, 날짜 불명 run hold를 전파했다. 파일 교체 후 내구성 실패와 정리 로그 쓰기 실패를 실제 삭제 건수·오류로 요약/화면에 남겼고, SGLang v0.5.21 토큰화 payload·CLI 출력·문서를 교정했다. `TMPDIR=/dev/shm .venv/bin/python -B -m unittest discover -s tests -v`는 개인 시험 8개를 포함한 290개 중 224개 통과·로컬 소켓 제한 66개 건너뜀·실패 0개였다. Git 추적 시험은 282개(`tests/test_claude_review.py` 제외)이다. `git diff --check`·대시보드 JavaScript 구문 검사도 통과했다. 실제 SGLang·소켓 HTTP 재확인은 남았고, 서버·Docker·kind·kubectl·설치는 조작하지 않았다. Git 기준점은 `6d4f9f9`, 커밋·푸시하지 않았다.
- 2026-10-24 Codex가 Claude 구현분 3개 커밋(`d3aa226` 정책 1.2, `baa928f` SGLang 준비·목록 문서, `6d4f9f9` 보존·지표 대응표)을 교차 검수해 필수 5건·권장 3건을 지적하고 수정했다. Claude가 소켓 허용 환경에서 재검수했다: 비해시 정책·backend 값 재현이 INVALID_POLICY/ValueError로 바뀜, 전체 unittest 290개(추적 282개) 통과·skip 0, `mvp-acceptance` 31개 통과. 위 세 묶음의 교차 검수는 완료됐다.
- 2026-10-24 3차 개발의 1차로 `docs/내부_기록형식.md`를 추가했다. 코드에서 확인한 schema 보유/미보유 기록·입력·보고서와 §6.1~6.4 변환, 연결 키, 빈 값·민감정보 원칙을 정리했고 D-03 이후에만 외부 형식을 매핑한다고 명시했다. `src/`의 schema 문자열 상수가 문서에 모두 있는지 확인하는 AST 회귀시험을 추가했다.
- §9.8 성능 비교 도구 `tools/perf_compare.py`와 `runtime-perf-plan/1.0` 예시를 추가했다. 운영자가 준비한 세 Gateway URL만 B0/B1/B2 고정 3블록으로 호출하고 monotonic E2E/TTFT·usage 출력 token·성공/오류·완료 req/s와 블록별 증감률을 O_EXCL JSON/Markdown/raw로 남긴다. 원문은 저장하지 않고 외부 vLLM benchmark는 미설치·미호출 상태의 연결 자리만 둔다. 정식 GPU 수치는 미측정이다.
- `TMPDIR=/dev/shm .venv/bin/python -B -m unittest discover -s tests -v`는 개인 시험 8개를 포함한 295개 중 228개 통과·로컬 소켓 제한 67개 건너뜀·실패 0개다(개인 시험 제외 287개). 새 socket-free 순서·계산·출력 시험과 schema 문서 회귀는 통과했고, 새 가짜 HTTP 서버 성능 시험 1개는 같은 소켓 제한으로 건너뛰었다. `git diff --check`도 통과했다. 서버·Docker·kind·kubectl·설치·커밋·푸시는 조작하지 않았고 Git 기준점은 `ae7b03b`이다.
- 2026-10-24 1차(내부 기록 형식 문서·성능 비교 도구, Codex 구현)를 Claude가 실제 모의 Gateway 3개에 붙여 검수했다. 가짜 HTTP 시험에서 드러나지 않던 결함 2건을 고쳤다: ① 측정 요청이 항상 `temperature`를 보내 기본 프로필(D-09)에서 전부 400 `UNSUPPORTED_FIELD` — 선택 항목으로 바꾸고 생략 시 보내지 않음, ② 응답 본문·`[DONE]` 직후 다음 요청을 보내 Gateway가 슬롯을 반환하기 전 429 — 측정 시각은 본문 끝/`[DONE]`으로 두고 close-delimited 응답은 서버 종료까지 기다린 뒤 다음 요청. 실제 Gateway 회귀시험을 추가했고(수정 전 실패 확인), 기존 연결 상한 시험의 고정 0.05초 대기를 슬롯 반환 대기로 바꿔 전체 실행 간헐 오류를 없앴다. 재실행 결과 207건 전부 성공·3블록 ok·원문 미저장, 전체 unittest 296개(추적 288개) 통과·skip 0. 커밋·푸시 전.
- 2026-10-24 2차(API 인증 프레임 + F1 대상 등록 프레임)를 사용자 지시로 Claude가 직접 구현했다(Codex 사용 한도 중, **Codex 교차 검수 전**). `runtime-auth/1.0`(`--auth-config`, bearer 토큰 SHA-256 저장, 역할·주체별 허용 모델, 401/403 엔진 미전달, 설정 오류 503 `AUTH_UNAVAILABLE`, `auth hash-token`)과 `runtime-target/1.0`(`--target`, 정책 target_id 불일치·등록 오류 503 판정 보류, 엔진/모델/이미지 digest 비교, `--target-pod-status` kind 전용 읽기 전용 Pod 조회, `runtime-target-snapshot/1.0` 인스턴스 이력, 요청 기록 `auth`·`target`)을 추가했다. 설정을 주지 않으면 기존 동작과 같다. 전체 unittest 317개 통과(skip 0, 추적 309개), `mvp-acceptance` 31/31. 로컬 kind에서 serving-engine Deployment 재시작 시 `instance_changed`/`pod_replaced` 기록과 인증 401/200을 확인했다(가짜 엔진 Pod가 새 Pod로 바뀜). 미확인: 실제 인증 연동(D-02)·계정(D-03), 조회 API·대시보드 인증, 정식 cluster.
- 2026-10-24 3차(SP3 단계별 실행·SP4 작업별 통신·SP1 모델/캐시 보호)를 Claude가 직접 구현했다(**Codex 교차 검수 전**). `runtime-enforcement-intent/1.1`(1.0 결과물 바이트 동일)에 acquire·test_receivers·engine management_port·observer_kube_api·assets(캐시 세대·UID)·phase_binaries·suspect_binaries를 추가하고, Cilium acquire/observer/receiver 정책, TG-ASSET-02, 캐시 루트 감시와 현재/다른 세대 분류, `pod-security-candidate.json`, `enforce cache-check`, 단계 시간표·기간 있는 승인 예외(`runtime-exec-exceptions/1.0`, `runtime-phase-timeline/1.0`)로 실행 이벤트 분류를 넣었다(관측·경보만). 전체 unittest 334개 통과(skip 0, 추적 326개), `mvp-acceptance` 31/31. 로컬 kind에서 acquire 송신·receiver 수신 정책 2개만 적용해 같은 receiver에 acquire 허용, engine·other-client 거부, acquire→engine API 거부를 확인한 뒤 시험 Pod·정책을 삭제했다. 이 과정에서 메인 실시간 수집 helper가 2026-10-24 13:07Z에 `tetragon follow exited with status 0`으로 멈춰 있던 것을 발견해 다시 켰다(원인 미확정, 그동안 대시보드는 관측 불가 표시). 미확인: 실제 엔진 단계별 정상 목록·단계 신호, resolved cache 경로, 읽기 전용 mount 실측, 실행 거부.
- 2026-10-24 실시간 수집 helper 자동 재연결을 Claude가 직접 구현했다(**Codex 교차 검수 전**). 하위 프로세스가 끝나면 그 묶음만 정리하고 `runtime-observation-feed-gap/1.0` 끊김 기록·heartbeat `reconnecting`(Gateway 관측 불가)으로 표시한 뒤 1~30초 backoff로 재연결하며, 재연결 전 context를 재확인하고 마지막 정상 확인 1초 전부터 Hubble `--since`·Tetragon `--since-time`으로 이벤트를 다시 요청한다. 로컬 kind에서 tetragon `kubectl logs`와 hubble port-forward를 강제 종료해 두 묶음 모두 약 1초 뒤 재연결, 끊긴 동안의 Tetragon 차단 이벤트와 Hubble DROPPED 6건 복구, Gateway 중복 0건을 확인했다. 첫 시도에서 감지 시각 기준 backfill이 최대 1초 이벤트를 놓칠 수 있는 문제를 찾아 마지막 정상 확인 기준으로 고쳤다. 전체 unittest 339개 통과(skip 0, 추적 331개). 메인 helper는 새 코드로 다시 켰다. 미확인: 로그 회전·Tetragon Pod 재시작 시 복구 완전성, 13:07Z 첫 종료의 원인.
- 2026-10-25 Claude가 직접 구현(**Codex 교차 검수 전, 커밋하지 않음**): (1) `docs/결정기록_D-02_인증연동.md` — LAB은 정적 bearer 토큰 프레임, 운영 연동 방식은 미결로 기록(계획서 원문 미수정). (2) 조회 API 인증 — `route_roles`에 `GET /api/*`를 넣으면 `/api/*` 전체에 401/403, 화면 파일은 공개, 인증 설정 오류 시 조회도 503 `AUTH_UNAVAILABLE`; 대시보드는 401에서 토큰 입력(탭 세션 저장)·`Authorization` 헤더·증거 내려받기 fetch. 기존 설정은 동작 불변. (3) `tools/k8s_lab_check.py --task-checks` L-07(SP4)·L-08(SP1) — 시험 Pod·정책을 실행마다 만들고 삭제. 로컬 kind에서 남아 있던 lab 정책을 지운 뒤 재실행해 L-01~05·L-07·L-08 passed, L-06 not_run(의도), overall partial(보고서 `~/.local/state/serving-runtime/k8s-lab/reports/k8s-lab-20261024T150501Z-702691.{json,md}`). L-07: 같은 receiver에 acquire만 연결, engine·other-client·acquire→engine API 차단. L-08: UID 10001 읽기 성공·쓰기 EROFS(errno 30)·캐시 세대 쓰기 성공(mount 결과, emptyDir 시험). 점검 중 메인 helper를 멈췄다가 다시 켰고 lab은 원래 정책 7개·Pod 4개 상태다. 전체 unittest 347개 통과(skip 0, 추적 339개).
- 2026-10-25 Codex가 `99163c8..acf07ab`의 Claude 구현 4개 커밋을 교차 검수해 필수 11건을 수정했다. 판정 순서·중복 인증 헤더·인증 설정 지문 노출·대상 이력 내구성, 1.1 selector/FQDN/자산 검증·캐시 검사·naive 시각 처리, 수집 자식 즉시 종료, L-07/L-08 정리 실패 판정·시험 Pod 최소 권한과 실제 spec 확인, 내부 기록 문서를 보강했다. 전체 unittest는 개인 시험 8개 포함 355개 중 276개 통과·소켓 제한 79개 건너뜀·실패 0개이며 추적 시험은 347개다. 실제 kind·kubectl·서버·수집 helper는 조작하지 않았고 설치·커밋·푸시하지 않았다. Git 기준점은 `origin/main=99163c8`, 검수 HEAD는 `acf07ab`이다.
- 2026-10-25 교차 검수 후속 수정으로 추론 요청 판정 순서를 정책→인증→대상→본문으로 되돌려 미인증 호출자에게 대상 상태를 노출하지 않고, 인증 성공 후 대상 보류 기록에는 주체가 남도록 했다(커밋하지 않음).
- 2026-10-25 `k8s_lab_check.py`가 안전 context 확인 뒤 L-01 전에 자체 렌더·적용 대상의 잔여 Cilium/Tetragon 정책만 삭제 완료까지 기다리고, 삭제 명령·결과와 관리 대상 외 정책 경고를 JSON/Markdown `실행 전 정리` 근거로 남기도록 보강했다.
- 2026-10-25 `mvp-acceptance`에 S4-01~06(인증·인증 오류·조회 보호·대상 판정 순서·가짜 자식 feed 재연결·정책 1.2)을 추가해 37개 사례로 늘리고 README·지원 목록을 맞췄다. 전체 unittest 359개 중 280개 통과·소켓 제한 79개 skip·실패 0개였고, 수용 시험 자체는 같은 소켓 제한(`Operation not permitted`)으로 시작하지 못했다. 커밋·푸시하지 않았다.
- 2026-10-25 관측 feed 재연결의 context 조회 `TimeoutExpired`를 gap 유지·1~30초 backoff 대상으로 처리하고 실제 context 불일치는 즉시 중단하도록 수정했다. 최초 조회 timeout도 traceback 없이 오류 종료하며, 전체 unittest 361개 중 282개 통과·소켓 제한 79개 skip·실패 0개다. 실제 helper·kubectl은 조작하지 않았고 Git 기준점은 `0ed00ff`, 커밋·푸시하지 않았다.
- 2026-10-24 21:30~22:10Z Claude가 18080 Gateway 새 코드·정책 1.2·등록 target에서 Docker vLLM CPU v0.29.0/Qwen2.5-0.5B-Instruct를 시험했다. 대상은 4개 match·`model.revision` unknown이었다. validate 정상 4필드와 passthrough `stop_token_ids`·`logit_bias`는 200, 범위 밖 `temperature`·문자열 `seed`는 400 `INVALID_EXTRA_FIELD`, `n=2`·`best_of`는 Gateway 400으로 엔진 미전달, 잘못된 `response_format`의 엔진 400은 Gateway 502 `ENGINE_BAD_RESPONSE`였다. `logprobs` 3회는 EngineCore timeout/엔진 정지 2회와 무응답 1회로 미완료이며 CPU 결함과 메모리 부족 중 원인은 미확정이다. 첫 시도는 RAM 7GB 중 6GB+·swap 2GB 소진 상태에서 기본 요청도 120초 504였고, kind pause 재시험 뒤 unpause했으며 그 사이 Tetragon이 OOM kill로 1회 재시작했다. 시험 뒤 18080은 기본 정책+target으로 재시작하고 vLLM 컨테이너는 정지했다.
- 2026-10-25 SP4 read-only 관리 listener를 구현했다. 기본 loopback bind, GET/HEAD·정확한 허용 경로, 본문/경로 변형/그 밖의 메서드 거부, 클라이언트 헤더 격리, 선택 엔진 credential 파일, timeout 504·연결/응답 오류 502, 응답 크기 상한, 내용·헤더 없는 최소 JSONL을 제공한다. CLI·단위/모의 HTTP 시험과 README의 intent 1.1 `engine.management_port` 연결을 추가했으며 실제 kind 사이드카는 미확인이다.
- 엔진 입력 오류 출처 보존은 계획서 §4.2를 근거로 결정·구현했다. 응답 시작 전 일반 엔진 4xx와 429는 상태를 보존하고 `ENGINE_REJECTED`/`source: engine`으로 Gateway `DENIED`와 분리한다. 엔진 401/403은 클라이언트 원인이 아닌 upstream 자격·권한 설정 오류이므로 502 `ENGINE_AUTHORIZATION_FAILED`/`ERROR`로 반환하고 원래 상태는 `engine_http_status` 기록에만 보존한다. 일반 4xx 메시지는 제한·정리한 클라이언트 응답에만 쓰고 401/403 본문은 노출하지 않는다. 엔진 5xx·잘못된 JSON·redirect는 502를 유지한다.
- 2026-10-25 Claude 소켓 검수의 두 실패를 수정했다. 관리 listener는 Python 3.12가 `//`를 접기 전 `requestline`의 원본 request-target으로 허용 판정·엔진 경로·최소 기록을 처리하고 절대 URI도 거부한다. S4-05는 초기 collector `ok`를 최대 10초 기다려 확인한 뒤에만 장애 전환하며, 전체-suite 스케줄 지연이 정상 snapshot을 `stale`로 만들지 않도록 시험 신선도 구간을 30초로 분리했다(요구하는 `ok`→`error`→`ok` 판정은 유지).
- 전체 unittest는 개인 시험 8개 포함 372개 중 287개 통과·소켓 제한 85개 skip·실패 0개(개인 시험 제외 364개 중 279개 통과·85개 skip)다. `mvp-acceptance` 37개는 소켓 생성 제한(`Operation not permitted`)으로 시작하지 못했다. `git diff --check`는 통과했다. Git 기준점은 HEAD `ce2edd5`이며 설치·kubectl·Docker·kind·실행 중 서버/helper 조작·커밋·푸시는 하지 않았다.
- 2026-10-25 Codex가 F7 승인 확인 틀과 엔진 4xx 출처 보존을 구현했다. `ApprovalVerifier` 교체 인터페이스와 `runtime-policy-approvals/1.0` LAB SHA-256 대조(요청별 파일 재검사, 불일치·항목 없음·손상·만료 503, 미설정 호환), 정책 상태·시작 이력·대시보드 표시를 추가했으며 실제 키·서명·승인 서비스는 만들지 않았다. 엔진 거부는 `ENGINE_REJECTED`로 요약 집계에서 Gateway 차단과 분리했고 upstream 인증 오류는 `ERROR`, 429는 upstream 용량 거부로 분류했다. 전체 unittest 377개 중 290개 통과·로컬 소켓 제한 87개 skip·실패 0개였고 `mvp-acceptance` 37개는 소켓 생성 제한(`Operation not permitted`)으로 시작하지 못했다. 실제 승인/서명 검증기 연동과 실제 CPU/GPU 엔진 4xx 재시험은 남았다. Git 기준점은 HEAD `863494d`이며 커밋·푸시하지 않았다.
- 2026-10-25 Claude 소켓 허용 검수에서 승인 틀은 문제 없고 unittest 377개·수용 시험 37개·node/diff 검사가 통과했으나 엔진 401/403을 클라이언트에 그대로 반환하는 분류 1건이 지적됐다. Codex가 일반·stream 모두 502 `ENGINE_AUTHORIZATION_FAILED`/`ERROR`로 고치고 원래 상태를 기록에만 보존했으며 엔진 본문 비노출, Gateway 인증 `DENIED`·엔진 입력 `ENGINE_REJECTED` 집계 분리를 시험에 고정했다. 429 `Retry-After`는 HTTP-date를 전달하지 않고 10자리 이하 정수 초만 정규화해 전달한다. 수정 후 이 환경의 전체 unittest는 377개 중 290개 통과·소켓 제한 87개 skip·실패 0개다. 실제 소켓 HTTP·수용 시험의 수정 후 Claude 재검수가 남았고 Git 기준점은 HEAD `863494d`, 커밋·푸시하지 않았다.
- 2026-10-25 Codex가 F4·SP1의 엔진 결합 여부를 확인했다. baseline/manifest 비교·재검증, 자산 해시, 캐시 세대와 Cilium/Tetragon 후보는 이미 공통이었다. 가짜 SGLang 수용 사례 2개(F4 drift, SP1 자산 변경)와 `sglang/...` 캐시 세대 단위 시험을 추가했고, 실제 SGLang 검증으로 세지 않는다. SGLang v0.5.21 모델 관측은 `/get_model_info`만 사용하며 `/server_info`는 호출하지 않고, 미수집 버전과 max model len을 각각 `not_collected`·`unknown`으로 둔다.
- SP3 `enforce phase-signals`를 추가했다. 제공된 vLLM CPU v0.29.0 로그의 요청 줄을 제외한 표지만 fixture로 두고 loading/compiling·재시작을 `runtime-phase-timeline/1.0`의 `engine_signal`로 변환한다. 연도·UTC offset은 필수 입력이고 처리/미해석 줄 수·마지막 위치·생략 경계를 남긴다. 제공 로그의 `Application startup complete.`에는 시각이 없어 inferring 경계를 추정하지 않았다. GPU compile/CUDA graph와 SGLang 패턴은 미확인/미지원이다. ExecPolicy 연결 시험은 통과했다.
- 공통 unittest는 개인 시험 8개 포함 384개 중 297개 통과·소켓 제한 87개 skip·실패 0개(추적 시험 376개)다. `mvp-acceptance` 39개는 소켓 생성 제한으로 시작하지 못했다. JavaScript 구문과 `git diff --check`는 통과했다. 실제 SGLang·GPU 단계 표지·소켓 수용 시험이 남았고, 실행 중 서버/helper·Docker/kind/kubectl·설치·금지 파일은 조작하지 않았다. Git 기준점은 HEAD `68dff9e`, 커밋·푸시하지 않았다.
- 2026-10-25 Codex가 SP3 `phase-signals --follow`를 추가했다. 파일 교체·잘림 또는 binary stdin을 줄 단위로 읽고 `runtime-phase-timeline/1.0`을 0600 임시 파일+`os.replace`로 갱신하며, 기존 출력은 명시적 overwrite/resume만 허용한다. compile/init 뒤 선택 health 200의 Gateway 쪽 수집기 UTC 시각이 로그 경계보다 뒤일 때만 `basis: engine_signal` inferring을 열고 재시작 로그에서 닫는다. 같은 1.0의 선택 extraction 필드에 follow/health/시계/재개 상태만 확장했고 기존 배치 출력·파서는 유지했다. 가짜 입력·시계·health, 파일 교체/잘림, 재시작, 요청 줄 위장 신호·원문 미저장, ExecPolicy 재읽기 연결 시험을 추가했다. 전체 unittest 388개는 실패 0·소켓 제한 88개 skip이었다. `mvp-acceptance`는 소켓 제한(`Operation not permitted`)으로 시작하지 못했고 `git diff --check`는 통과했다. 실제 vLLM follow/health는 미확인이다. Git 기준점은 HEAD `62fab19`, 커밋·푸시하지 않았다.
- 2026-10-25 개발계획서 v1.4 §10.3~10.4와 10/29·11/2~3 일정에 맞춰 실제 CLI·권한·판본·증거 경로·오류·기본 복원·기능별 미확인과 인수자 직접 확인표를 `docs/인계_안내.md`에 정리했다. 정식 환경 확인이나 담당 배정을 뜻하지 않는다.

## 다음 준비 작업

- AWS dev 기능 연결 확인 뒤 16 vCPU 승인 대기 중인 reference 재실행과 platform AWS 판정 화면 검토가 다음 작업이다. 정식 Kubernetes 1.36.5 환경 검증, 정책 로드 상태의 Tetragon 재시작·업그레이드 재현, 앞 단계 담당자의 정책값·승인 구성·공통 결과 형식도 남았다.

## 갱신 방법

확인한 날짜·변경·실행 명령·실제 결과·증거 위치·미확인·다음 작업을 짧게 유지한다.
계획과 실행 결과를 구분하고, 오래된 다음 작업은 교체한다. 긴 로그는 원본 경로만 남긴다.
- 2026-10-25 푸시 전 Claude 직접 검수(계획서 대조)에서 충돌 2건을 재현하고 **Claude가 직접 수정했다(Codex 교차 검수 전)**. (1) §7.4 "단계는 신뢰 경로에서 입력, 단계 추정만으로 완화 금지" — 엔진 로그 기반 `engine_signal` 단계만으로 허용 범위를 넓히던 것을 막고 `phase_signal_unverified`(확인 필요)·`phase_trusted`·`exception_candidate_id`를 추가했다(모든 단계 공통 승인 실행 파일은 그대로 허용). (2) §7.3 "새 유효성 조회 불가 시 마지막 승인 유지" — 실행 중 승인 목록을 읽지 못하면 즉시 503이던 것을 같은 정책 바이트·유효 기간 안에서 `last_verified`로 유지하도록 바꿨다(시작 시 목록 없음·명시적 항목 없음·불일치·만료는 503 그대로). 함께 README 시험 수(추적 384·수용 39)와 SGL 사례, 인계 안내의 정책 1.2 §4.10 차이를 고쳤다. 이전 줄의 "Claude 재검수 남음"은 이미 소켓 허용 환경에서 재검수 완료(unittest 377·수용 37 통과)다. 소켓 허용 환경에서 unittest 392개(추적 384개) 통과·skip 0, `mvp-acceptance` 39개 통과, `node --check`·`git diff --check` 통과. 푸시하지 않았다.
- 2026-10-25 대시보드를 왼쪽 사이드바 6개 영역(현황·요청·정책·대상·구성·관측·통제 적용)으로 나눴다(**Claude 직접 구현, Codex 교차 검수 전**). 같은 주소(18080) 한 화면 안에서 메뉴로 전환하고 `#영역` 주소로 새로고침해도 유지하며, 메뉴마다 경고 수 배지와 환경 표시(Gateway 쪽 docker-cpu, 관측 쪽 kind-lab-wsl)를 단다. 좁은 화면에서는 위쪽 가로 메뉴로 바뀐다. 캡처 확인 중 L-07·L-08이 든 lab 보고서를 대시보드가 "L-01~L-06만 허용" 검사로 읽지 못하던 기존 버그를 찾아 L-07·L-08을 선택 사례로 받도록 고치고 회귀시험을 추가했다. unittest 393개(추적 385개) 통과·skip 0, `node --check`·`git diff --check` 통과. 커밋하지 않았다.
- 2026-10-25 대시보드 「관측」 영역(수집원·Hubble 통신·Tetragon 프로세스·파일)과 「통제 적용」 영역(Cilium 정책 적용 상태·kind lab 시험 결과)을 도구별 탭으로 나눴다(**Claude 직접 구현, Codex 교차 검수 전**). 탭마다 건수·경고 배지(수집원 문제 수, Tetragon 목록 밖·의심·확인 필요 실행 수)를 달고, `#observe/hubble`처럼 주소로 탭을 유지한다. 커밋하지 않았다.
- 2026-10-25 대시보드 현황에 그래프 4개(결과 비율, 거부·오류 사유 상위, 관측 규칙별 사건, 시간별 요청)를 넣었다(**Claude 직접 구현, Codex 교차 검수 전**). 사용자 승인으로 Chart.js 4.5.1(MIT, 2025-10-13 공개; 3일 전 공개된 4.6.1은 제외)을 npm 레지스트리 tarball의 sha512 integrity와 대조해 받고 `mvp/static/vendor/`에 `chart.umd.min.js`(SHA-256 `48444a82…f54a`)와 LICENSE를 고정했다. 네트워크 요청·`eval` 코드가 없음을 확인했고 CDN은 쓰지 않는다. Gateway 정적 허용 목록·패키지 데이터·외부 기능 목록·README를 갱신하고 고정 해시·로컬 로드 시험을 추가했다. 요청 기록에 지연 시간 필드가 없어 지연 시간 그래프·열은 아직 없다. 커밋하지 않았다.
- 2026-10-25 대시보드 디자인을 Langfuse 홈 구성을 참고해 다시 짰다(**Claude 직접 구현, Codex 교차 검수 전**). 강조색은 보라 계열 하나와 회색 단계만 쓰고 빨강·주황은 오류·경고에만 쓴다. 그라데이션·카드 색 띠·큰 모서리·그림자를 뺐다. 왼쪽 메뉴는 기능별 묶음(개요/요청 통제/관측/통제 적용/업데이트 검증)으로 나눴고, 업데이트 검증은 "준비 중" 화면만 있다. 아래쪽 설정·사용자는 실제 상태만 보여 준다(인증 미적용 등, 지어낸 값 없음). 현황은 요약 카드 3개(요청 결과 막대, 거부·오류 사유 표, 관측 규칙 표)와 탭이 있는 그래프 카드 2개(시간별 요청 전체/결과별, 구성 비율 요청 결과/관측 규칙)로 바꿨다. 오른쪽 위 다크/라이트 전환은 처음엔 운영체제 설정을 따르고 선택을 브라우저에 저장하며(저장 불가여도 동작), 그래프 색도 함께 바뀐다. Chart.js 4.5.1 외 새 라이브러리는 없다. 커밋하지 않았다.

## 2026-10-25 byul 통합: Gateway 자체 대시보드 제거

- byul 저장소에서 사용자 요청으로 `src/serving_runtime/mvp/static/`(index.html·app.css·app.js·vendored Chart.js)을 지우고 Gateway의 `/`·`/static/*` 서빙을 없앴다(이제 404 JSON). 화면은 byul `platform/`이 조회 API를 GET으로 읽어 보여준다. 조회 API·기록 형식은 바꾸지 않았다.
- 화면 내용만 검사하던 시험 7개를 지우고 `/`가 404인지 보는 시험으로 바꿨다. `unittest discover -s tests` 379개 중 378개 통과, 남은 1개(`test_mvp_http_security` 연결 상한 시험의 Connection reset)는 변경 전에도 같은 환경(macOS)에서 실패했다.
- 이 변경은 Codex 교차 검수 전이다(byul에서 Claude가 직접 수정).

## 2026-10-26 contracts 대조와 LAB 정책 묶음 수신

- runtime 결정(2026-10-26)으로 자산 root 미순회 3개 개수와 정책 없음 2개 개수를 0 대신 `null`로 보내고, contracts·platform·웹 표시를 해당 필드에만 맞춰 반영했다.
- **Codex 구현, Claude 검수 완료.** runtime 결정에 따라 Tetragon file_access outcome과 `declared`·`fake_backend_test` 등급을 contracts 표와 platform 변환기에 반영하고 대조 기록을 작성했다. contracts는 DRAFT/0.x 그대로다.
- `bundle verify --bundle-dir`와 `mvp --policy-bundle`을 추가했다. bundle 구조·중복 JSON 키·경로/링크 탈출·bytes/SHA-256·내부 schema·기간·대상을 시작 시 검증하고 역할별 적용 여부를 `/api/status`·`/api/policy`에 공개한다. bundle 자기신고 승인은 신뢰하지 않고 기존 승인 목록 검증을 사용한다.
- runtime 전체 unittest는 397개 중 308개 통과·소켓 제한 89개 skip·실패 0개다. 새 bundle 시험 18개와 platform runtime 변환시험 40개는 전부 통과했고 contracts 검사는 `schemas=12 examples=11 errors=0`이다. platform 전체 pytest는 기존 venv에 update의 `guard`와 `yaml`이 없어 수집 단계에서 중단됐다.
- Claude가 샌드박스 밖에서 재시험: runtime unittest 399개 전부 통과(skip 0), platform `uv run --with ../../update pytest` 96개 통과, web vitest 21개·`tsc -b` 통과, contracts 검사 오류 0. 예시 묶음(`contracts/examples/bundle`)으로 `mvp --policy-bundle` 실행 시 상태에 묶음 표시·요청 200, 정책 파일을 바꾼 묶음은 SHA-256 불일치로 시작 거부를 확인했다.
- scratch runtime에서 같은 정책 ID·판본의 내용만 바꿔 `policy_content_changed` 재검증 요청 1건과 다음 재시작의 `condition_cleared`를 만들고, platform의 `byul-revalidation-request/0.2` 변환 및 `revalidation-request`·`runtime-status` schema 오류 0건을 확인했다. `asset_changed`·`config_drift` trigger는 이번 실행에서 일으키지 않았다.
- 미검증: 원격 bundle 전달, hot reload, 실행 중 묶음 유효 기간 재확인. platform은 지정한 4개 미측정 개수만 `null`로 보존하고 나머지 `_nonneg` 처리는 유지한다.
- platform README의 실제 확인 시험 수를 api 96개·web 21개로 바로잡았다. **Codex 작성, Claude 검수 완료.**
- contracts 대조 기록에 `policy_content_changed` 재검증 생성·해소와 계약 오류 0건을 반영했다. **Codex 작성, Claude 검수 완료.**
- update의 사건→`check_id` 매핑 확인용 `docs/runtime_규칙목록.md`를 실제 규칙 코드와 contracts 표에서 작성했다. **Codex 작성, Claude 검수 완료.**
- 계획서의 RT 46개를 현재 unittest·수용·kind LAB·CPU 근거에 연결한 `docs/인수시험_대응표.md`를 작성했다. **Codex 작성, Claude 검수 완료.**
- Task 1: kubectl/cilium 호출의 `--kube-context`(기본 `kind-runtime-lab`) 명시와 context 검증, 비-kind lab 변경 작업의 `--allow-non-kind-context` 이중 확인·경고를 구현했다. **Codex 구현, Claude 검수 완료.**
- Task 2: 공통 요청 파이프라인을 쓰는 `POST /v1/completions`, 단일 문자열 prompt 검증·tokenize·route 기록·모의/가짜 backend를 구현했으며 실제 vLLM v0.31.0·SGLang schema는 미확인이다. **Codex 구현, Claude 검수 완료.**
- Task 3: Hubble lost-events를 flow와 분리해 누적/source별/최근 시각/freshness `partial` 상태로 표시하고 contracts·platform API·웹까지 전달했다. 실제 Hubble loss 발생 환경은 미확인이다. **Codex 구현, Claude 검수 완료.**
- Task 4: `exec_verdict` 설명을 실제 ExecPolicy 순서에 맞추고 completions 사유 코드와 Hubble loss 상태 기록을 규칙 목록에 추가했다. **Codex 구현, Claude 검수 완료.**
- Task 5: 위 Task 1~4의 구현 상태를 2026-10-26 절에 작업별 한 줄로 기록했다. **Codex 구현, Claude 검수 완료.**
- Codex가 사용 한도로 마지막 시험 단계에서 멈춰 **Claude가 이어받아 마무리했다(Claude 구현분, Codex 교차 검수 전)**: ① `runtime-auth/1.0`에서 chat route는 계속 명시 필수, `/v1/completions`는 생략 시 기본 `inference` 역할로 보호(Codex안은 chat 누락도 기본값으로 채워 기존 fail-closed 검사를 없앴음) ② `--kube-context`는 생략 시 대상 등록의 `kubernetes.context`를 쓰고, 둘이 다르면 시작 거부(교차 검수 지적 5) ③ `docs/내부_기록형식.md`에 `runtime-hubble-loss/1.0` 추가, socketpair 시험 helper에 completions handler 전달. 샌드박스 밖 재시험: runtime 406개 전부 통과(skip 0), platform 97개, web vitest 21개·`tsc -b`, contracts 오류 0.
- 2026-10-26 Claude 구현분 교차 검수(Codex, 읽기 전용)에서 나온 지적 7건을 계획서·코드로 확인하고 **Claude가 직접 수정했다(Codex 사용 한도 중, Claude 구현분·Codex 교차 검수 전)**: ① 대상 등록 선언값이 관측과 다르면 `TARGET_MISMATCH`, 확인 못 하면 `TARGET_UNVERIFIED`, 등록된 Pod 조회가 정상이 아니면 `TARGET_UNAVAILABLE`로 신규 요청 503 보류(§4.1·§7.3, 이전에는 표시만 함; `model.revision`은 engine API로 확인 불가라 제외) ② 실시간 수집 helper가 Tetragon raw 파일에 쓰기 전에 `process`·`parent`·`ancestors`의 `arguments`를 지우고 JSON 아닌 줄은 버림(§6.5) ③ 승인 목록 동시 확인에서 늦게 끝난 옛 성공이 새 목록의 명시적 miss를 덮어써 철회된 승인이 `last_verified`로 되살아나던 경합을 읽기 순번으로 막음(수정 전 실패하는 재현 시험 추가) ④ context 사전 검사의 `/tokenize`에 엔진으로 보내는 승인 추가 필드를 함께 보내고, tokenizer가 거부하면 `CONTEXT_CHECK_UNAVAILABLE` 503 ⑤ `--kube-context`가 대상 등록 context와 다르면 시작 거부(위 커밋에 반영) ⑥ cache check가 cache root·중간 디렉터리의 소유자·group/other 쓰기를 `ancestor_problems`로 판정 ⑦ observer의 kube-apiserver 송신을 `observer_kube_api_port`(기본 6443) 하나로 제한(§4.3). runtime unittest 417개 전부 통과(skip 0), `mvp-acceptance` 39개 통과, platform 97개 통과. 미확인: 실제 Tetragon export의 인자 필드 전체 목록, 정식 cluster의 API server port, 보류가 실제 GPU 엔진 기동 시간에 주는 영향.
- 주의: 이번 수정 전에 만든 kind lab raw 파일 `~/.local/state/serving-runtime/k8s-lab/live/tetragon.jsonl`에는 프로세스 인자가 남아 있을 수 있다. 삭제 여부는 사용자 확인 사항이다.
- 2026-10-26 정식 GPU lab 배포 파일 초안 `deploy/gpu-lab/`(base + vllm·sglang overlay, kustomize)과 Gateway 이미지 정의 `deploy/gateway/Dockerfile`을 작성했다(**Codex 사용 한도 중 Claude 구현, Codex 교차 검수 전**). namespace는 Pod Security restricted라 hostPath 대신 `/srv/serving/…` local PV·PVC를 쓰고, 역할별 SA(observer만 토큰)·UID(I.4의 10001~10003, Gateway 10000·observer 10004는 프로젝트 선택값)·모델 읽기 전용·캐시 세대 subPath·관리 수신부 사이드카·정지 상태의 모델 반입 Job을 둔다. 노드 이름·이미지 digest·모델/캐시 경로·반입 명령은 `REPLACE…`로 비워 두었다. `kubectl kustomize`로 두 overlay 렌더링(각 24개 객체)만 확인했고 클러스터에는 적용하지 않았다. Gateway 이미지는 표준 라이브러리 코드만 복사하고 최소 dist-info를 만들어 `--network none` 로컬 빌드·`--version`·`bundle verify`·읽기 전용 FS 실행·UID 10000을 확인한 뒤 이미지를 지웠다. 미확인: 실제 클러스터 적용과 restricted 통과, 엔진 판본별 실행 인자·읽기 전용 root FS 기동, observer 수집 프로그램, Gateway의 클러스터 노출 방식(현재 127.0.0.1 bind).

## 2026-10-26 AWS 설치 묶음에 맞춘 runtime 배포 층

- AWS-08의 credential-free 대상 추적 gap을 host `runtime-target-pods-snapshot/1.0` → Gateway `--target-pod-snapshot` 경로로 연결하고, rollout 인수 판정에 새 instance_id와 `instance_changed`(`pod_replaced`)를 필수화했다. writer 5초·kubectl timeout 4초·Gateway poll 5초의 최악 age 14초가 stale 15초 미만이 되도록 기본값과 CLI 상한을 고정했다. 전용 snapshot 시험 11개가 통과했고, Codex 샌드박스에서는 runtime unittest 460개 중 96개가 소켓 제한으로 건너뛰어졌고, Claude가 샌드박스 밖에서 460개 전부 통과(skip 0)·platform pytest 169개 통과·`kubectl kustomize` 7개 객체를 확인했다. **Codex 구현, Claude 검수 완료(갱신 간격 대비 stale 판정으로 503이 간헐 발생하는 문제 지적 → 수정)**. 실제 AWS 실행은 안 함.

- AWS 첫 완료 기준 인수 도구·시험·런북(AWS-01~AWS-08)을 추가했다. **Codex 구현, Claude 검수 완료(판정 규칙 2건 지적 반영, 샌드박스 밖 runtime 446·platform 169 통과)**.
- AWS 공통 GPU 시험환경 설치 묶음(`aws-handoff-20261026`, 16:28판)과 대조해 `deploy/gpu-lab/`을 그 위에 얹는 층으로 다시 만들었다(**Codex 사용 한도 중 Claude 구현, Claude 다른 세션 검수 예정, Codex 교차 검수 전**). 엔진·namespace·SA·엔진 격리 정책은 묶음 것을 쓰고, Gateway(SA `gateway`, hostPath로 정책 묶음·인증 Secret·관측 기록 읽기 전용·기록 폴더, `--observation-feed`), 엔진 관리 수신부 사이드카 strategic patch(8001), `serving-engine-management` Service, observer→8001 NetworkPolicy, observer 자리, 시험 Pod, `intent.aws-lab.json`, 호스트 수집기 systemd 서비스를 둔다. 이전 base/overlay(엔진 중복·restricted·local PV·UID 10001)는 지웠다.
- 엔진 UID는 사용자 결정으로 AWS 묶음과 같은 2000:0(vLLM 0.31.0 공식 비root)을 따르고 `docs/결정기록_엔진UID.md`에 계획서 10001과의 차이를 남겼다. 계획서 원문은 고치지 않았다.
- 수집기 `tools/k8s_observation_feed.py`에 `--reader-group GID`를 추가해 feed·heartbeat·gap을 한 그룹에 0640(폴더 0750)으로만 공유한다. 생략 시 기존처럼 소유자만 읽고, 새 feed 파일도 0600으로 만든다.
- 확인: `kubectl kustomize deploy/gpu-lab` 7개 객체, AWS 묶음 `engine.yaml.template`(임시값 대입)에 사이드카 patch를 합쳐 엔진 UID 2000:0·RuntimeClass·GPU 요청이 유지되고 사이드카만 더해지는 것, `enforce render --intent deploy/gpu-lab/intent.aws-lab.json` 10개 생성.
- 미확인·요청: (해결됨, 아래) 묶음은 캐시 세대 폴더를 `/cache`에 붙여 TG-CACHE-01(`/var/cache/serving/…` 구조)이 사건을 못 잡았다. 03 재실행 때 client-side apply라 사이드카가 남아 smoke 증거에 섞일 것으로 본다(클러스터 미검증, 떼고 붙이는 절차 기록·템플릿 반영 요청). Pod 보안 후보 파일은 엔진 그룹을 2000으로 가정해 AWS(GID 0)에 그대로 쓰지 않는다. 실제 AWS 적용·hostPath 그룹 읽기·사이드카 기동은 미실행.
- 같은 날 다른 Claude 세션(읽기 전용) 검수 지적 6건을 반영했다: 03 재적용과 patch의 상호작용 설명 정정(사이드카가 남음, 떼는 명령 추가), 수집기가 `--reader-group` 그룹에 속하지 않으면 명확한 오류·종료 코드 2로 멈추고 systemd는 재시작하지 않음(`RestartPreventExitStatus=2`, 사용자 매니저 재시작·확인 절차), 정책 묶음 `current`는 상대 링크·Gateway가 읽을 파일 권한 명시, 공유 권한 변경을 fd 기준(`fchown`/`fchmod`)으로, SSH 터널 로컬 포트 충돌(8080)·secret 생성의 `--context`·patch의 Recreate 재기동 명시. runtime unittest 통과. Codex 교차 검수 전.
- 사용자 결정으로 캐시는 우리 쪽에서 맞췄다(**Claude 구현, Codex 교차 검수 전**): intent 1.1에 선택 `assets.cache_mount_path`를 추가했다. 현재 세대만 컨테이너에 마운트하는 구조(AWS 묶음 `/cache`)에서 `protected_paths.cache_dir`는 이 값이고, Tetragon TG-CACHE-01과 exec-from-cache는 이 경로를 기준으로, 캐시 쓰기는 마운트 안이면 current·밖이면 outside로 분류한다. `cache_root`·세대 경로는 호스트 기준(`/srv/serving/cache/vllm/<digest>/<revision>/<profile>-profile-01`, 03과 같음)이라 `enforce cache-check`를 호스트에서 바로 쓴다. 지정하지 않으면 기존 동작과 같다. `intent.aws-lab.json`을 이 방식으로 바꾸고 환경 담당 요청 표에서 캐시 항목을 뺐다. 시험 5개 추가.
- 위 변경에 대한 다른 Claude 세션 검수(질문 3건 답변, 지적 1·경미 2)를 반영했다: 마운트 구성에서는 CACHE-02 근거가 runtime 사건이 아니라 "현재 세대만 마운트"라는 배치 사실임을 README·규칙 목록·내부 기록 형식에 적고, 엔진 Deployment의 cache hostPath·mountPath를 확인하는 명령을 배포 절차에 넣었다(runtime 자동 대조는 미구현). intent.py에 호스트/컨테이너 좌표 혼재로 기존 supply–cache_root 분리 검사가 의미를 잃는다는 주석, 프로파일 변경 시 `compile_profile` 교체, Pod 보안 후보의 캐시 환경변수 차이를 명시했다. 검수자는 03의 실제 소유자·권한(leaf 2000:0 0700, 중간 root 0755)과 cache-check ancestor 검사가 충돌하지 않는다고 확인(코드 기준 추론).
- AWS 인수 도구 검수 지적 2건을 반영했다(**Codex 수정, Claude 재검수 완료**). AWS-05는 팀 소유 `test-receiver` Pod IP에 대한 engine 거부와 `other-client` 허용 대조 및 Hubble 연결 증거로 판정하고 TEST-NET-1·IMDS는 보조로 내렸다. AWS-07은 clean stop 뒤 두 heartbeat `stopped`, API stale window 안의 collector non-ok, 재시작·회복으로 판정하며 gap은 보조로 내렸다. 전용 unittest 20개 통과; 실제 AWS 실행은 하지 않았다.

## 2026-10-27 CPU vLLM 통합 저장소 일회 점검 준비

- `deploy/cpu-lab/`에 vLLM(처음 v0.29.0 기준, 아래에서 v0.31.0으로 전환)·served model `qwen2.5-0.5b-instruct` 대상 등록, context/output 상한 정책, 승인 목록을 포함한 정책 묶음과 실행 절차를 추가했다. `tools/cpu_vllm_check.py`는 실행 중인 18180 Gateway에 CPU-01~14를 보내고, 선택한 경우에만 CPU-15 logprobs를 실행하며 저장소 밖 0700 폴더에 본문·토큰·원시 로그 없는 JSON/Markdown 증거를 남긴다. 전용 unittest 8개, 전체 468개 중 372개 통과·소켓 제한 96개 skip·실패 0개, bundle verify와 diff check가 통과했다. 실제 Docker/Gateway 시험은 Claude 검수 때 남았다. **Codex 구현, Claude 검수 전**.
- 사용자 결정으로 CPU lab을 vLLM v0.31.0(`vllm/vllm-openai-cpu:v0.31.0-x86_64@sha256:8024248339dc…`)으로 바꿨다. 기존 v0.29.0 컨테이너 `s3b-vllm-cpu`는 그대로 두고 새 컨테이너 `byul-vllm-cpu-031`을 쓴다. 이미지는 digest 고정으로 받았고, 이 노트북 CPU에서 `import vllm`이 되며 torch CPU capability가 AVX2로 잡히는 것까지 확인했다(엔진 기동은 미확인).
- Codex 교차 검수(Claude 구현분) 지적 5건과 vLLM 소스(ce6c241ddc, v0.29/0.31과 다를 수 있음) 대조 지적 5건을 반영했다: 승인 경합에서 늦게 끝난 옛 성공이 새 결과(철회)를 따르게 함, Pod 교체 순간 같은 snapshot의 instance로 비교, Tetragon 인자 제거 pump 실패·버린 줄을 heartbeat·collector 상태에 반영, cache 점검이 엔진 통과·쓰기 권한까지 확인, 수집 원본 파일 O_NOFOLLOW 열기, completions 엔진 로그 `cmpl-<id>-0` 매칭, completion 응답 최소 형식 검사(모의·가짜 backend도 vLLM 형식), 대상 model.id를 served model 목록 포함으로 비교, zeroday 추가 필드는 chat 경로에만, `/tokenize` 실패의 HTTP status·endpoint 기록. **Codex 구현(Codex 사용량 문제로 사용자 지시에 따라 마지막 단계에서 취소), Claude 마무리·검수.** 샌드박스 밖 runtime unittest 482개 전부 통과(skip 0), platform pytest 169개 통과, cpu-lab 정책 묶음 검증 성공.
- 2026-10-27 15:20(KST) 실제 CPU vLLM 일회 점검을 실행했다(**Claude 실행, Codex 교차 검수 전**). `byul-vllm-cpu-031`의 live `/version`이 `0.31.0`임을 확인하고 bundle verify 성공 뒤 18180 Gateway(`deploy/cpu-lab` 묶음·저장소 밖 auth)로 `tools/cpu_vllm_check.py`를 돌려 CPU-01~14 모두 `passed`, CPU-15 logprobs는 미지정으로 `not_run`, overall `passed`. 엔진 로그 매칭(chat `chatcmpl-<id>`, completions `cmpl-<id>-0`)과 CPU-03 엔진 abort는 docker 로그에서 관측됐다. Gateway 기록의 `backend_cancel`은 `not_observed`로 남아 엔진 abort 관측과 별개다. 보고서에 토큰 원문이 없음을 grep으로 확인했다. 노트북 메모리 부족으로 kind 클러스터 `runtime-lab-control-plane`을 정지하고 예전 18080 Gateway를 종료했으며, vLLM 컨테이너는 `docker update --cpus 4`로 제한한 상태에서 실행했다. 증거: `~/.local/state/serving-runtime/cpu-vllm/evidence/cpu-vllm-20261027T062005Z-2905275/report.{json,md}`. 미확인: GPU S3B, logprobs, kind 연동 관측과 함께 쓰는 구성.
- 2026-10-27 사용자 요청으로 문서를 정리했다(**Claude 작성, Codex 교차 검수 전**). 667줄 README 전체를 `docs/상세_안내.md`로 옮기고(내용 유지, 제목·링크만 수정) `README.md`는 현재 상태·빠른 시작·환경별 안내·문서 목록만 담은 요약으로 새로 썼다. `docs/인계_안내.md`는 기준일 10/27, byul `runtime/` 경로, platform 화면 확인 방법, §3.5 CPU vLLM v0.31.0 점검(cpu-lab), §3.8 AWS GPU lab(미실행), 기능별 확인 수준(F1·F2·F6·SP2)을 갱신했다.
- 2026-10-27 AWS dev 시험 노트북 준비(**Claude 실행, Codex 교차 검수 전**). ① `deploy/gpu-lab/README.md`·사이드카 patch 주석을 AWS 묶음 방침(03은 engine-only 기준, runtime 사이드카·정책은 별도 통합 층, 03 재검증 때만 반출→회수→scale 0→03→재적용)으로 통일하고 "엔진 템플릿에 사이드카 추가" 요청과 회수 목록을 정리했다. ② Gateway 이미지 `serving-gateway:rt-787bcfc`(linux/amd64, UID 10000, base `python:3.12-slim@sha256:12316deb…`(2026-10-26 빌드, 임시 DOCKER_CONFIG로 자격 helper를 우회해 새로 받음·사용자 Docker 설정 미변경), image ID `sha256:1779f567…`)를 빌드해 저장소 밖 `~/.local/state/serving-runtime/aws-lab/`에 tar(SHA-256 `fcdee189…`)와 빌드 기록(`*.build.txt`)으로 저장했다. ③ 같은 폴더에 AWS용 토큰 env(0600)와 해시만 든 `auth.json`(0600, `load_auth` state ok)을 만들었다. ④ 빈칸을 임시값으로 채운 사본에서 `kubectl kustomize`(7개 객체)와 `enforce render`(10개 후보), `load_target` 통과를 확인했다. 저장소의 `REPLACE…`는 템플릿으로 유지하고(Gateway tag·base digest는 빌드마다, 엔진 digest는 서버 03의 `images.lock`, 프로파일은 dev/reference 전환마다 달라짐) 서버 사본에서 채운다. unittest 482개 통과.
- 2026-10-28 사용자 결정으로 추가개발 후보 B를 진행해 D-09 기본 텍스트 profile을 기존 5개 필드와 정책에 선언한 bounded validate 필드로 확대했다. 정책 1.2 기본 profile은 숫자 min·max, 문자열 max_length, 배열 max_items·items 및 항목 범위를 필수화하고 object·passthrough를 거부하며, `zeroday-lab`·1.0/1.1·`max_n`·예약 필드는 유지했다. 기본 sampling 예시와 정책/경계값/엔진 미도달·기록 시험을 추가했다. Codex 샌드박스에서는 전체 unittest 494개 중 393개 통과·소켓 제한 101개 skip·실패 0개였고, Claude가 샌드박스 밖에서 `.venv/bin/python -B -m unittest discover -s tests`를 다시 실행해 494개 전부 통과(skip 0)했다. 새 시험 12개(`tests/test_mvp_default_extra_fields.py`, 소켓 HTTP 엔진 미도달 시험 포함)도 밖에서 전부 통과했다. 실제 vLLM·SGLang은 미확인이고 예시 `approval_ref`는 승인값이 아니다. 다음 작업은 후보 C 검토다. **Codex 구현·시험, Claude 검수 완료**.

## 2026-10-28 사용자별 vLLM prefix cache 분리 후보 A

- 사용자 결정에 따라 정책 1.2의 선택 `cache_isolation`(`off` 기본·`per_principal`)과 LAB 인증 principal별 HMAC-SHA256 `cache_salt` 자동 주입을 구현했다. 키는 `SERVING_RUNTIME_CACHE_SALT_KEY` 환경변수의 32바이트 이상 값만 사용하며 키·salt는 기록·상태·오류에 남기지 않는다. chat·completions 두 route에 Gateway 소유 필드로 마지막에 쓰고, 클라이언트 `cache_salt`는 활성 모드에서 정책 선언도 요청도 거부한다.
- 인증 미구성, 키 없음·짧음, vLLM 아닌 backend는 본문·엔진 전달 전 503 `CACHE_ISOLATION_UNAVAILABLE`로 보류한다. 기존 정책 → 인증 → 대상 → 본문 순서에서 cache 준비 판정은 대상 뒤·본문 앞이다. `mock`은 실제 cache 분리를 제공하지 않아 지원 불가이며, kind `vllm`인 가짜 HTTP backend만 주입 통합 시험에 사용한다.
- `/api/status`·`/api/policy`는 `cache_isolation`의 mode·ready·`unavailable_reason_code`를, 요청 기록·조회는 mode·applied만 표시한다. 예시 `examples/mvp/policy-v1.2-cache-isolation.json`의 approval_ref는 승인값이 아니다. platform 화면 코드는 변경하지 않았다.
- 후속 작업으로 contracts와 platform API 변환을 연결하고 platform 현황에 사용자별 캐시 분리 mode·준비 상태, 요청 상세에 적용 여부를 표시했다. Claude가 샌드박스 밖에서 contracts 검사 오류 0, platform API pytest 184개·web vitest 34개 통과, typecheck 성공을 확인했다. OpenAPI 재생성에 기존 framework nullable 표현 동기화가 함께 포함됐다. **Codex 구현·시험, Claude 검수 완료**.
- 전용 시험 `tests/test_mvp_cache_isolation.py` 10개 중 정책/HMAC 4개는 Codex 환경에서 통과했고, HTTP 소켓 통합 6개는 샌드박스 제한으로 skip됐다. 전체 unittest는 504개 중 397개 통과·소켓 제한 등 기존/신규 107개 skip·실패 0개였다. Claude가 샌드박스 밖에서 `.venv/bin/python -B -m unittest discover -s tests`를 실행해 504개 전부 통과(skip 0)했고, 새 시험 10개(HTTP 통합 6개 포함)도 전부 통과했다. Claude 검수에서 발견한 비문자열 `cache_isolation`의 예외 누출을 문자열 선검사와 배열·객체·숫자·boolean·null 회귀시험으로 수정했다. 시험 메서드 수는 그대로이며, 수정 후 Codex 재시험도 전용 10개 중 4개 통과·6개 소켓 skip, 전체 504개 중 397개 통과·107개 skip·실패 0개였다. 수정 후 Claude가 샌드박스 밖에서 다시 실행해 runtime 504개 전부 통과(skip 0)했다.
- 미확인: 실제 vLLM prefix cache 분리와 `prefix_cache_hits` 성질, GHSA-935w·GHSA-2823 잔여 엔진 결함, SGLang 지원, 성능 비용. 다음은 Claude 검수 후 CPU vLLM에서 principal A→B와 A→A의 `vllm:prefix_cache_queries`·`vllm:prefix_cache_hits` 성질 시험이다. **Codex 구현·시험, Claude 검수 지적 반영·재검수 완료**.
- 2026-10-29 Claude가 로컬 Docker `byul-vllm-cpu-031`(image `vllm/vllm-openai-cpu:v0.31.0-x86_64`, ID `sha256:8024248339dc…`, live `/version` `0.31.0`, `enable_prefix_caching=True`, Qwen2.5-0.5B-Instruct revision `7ae5576…`, `VLLM_CPU_KVCACHE_SPACE=1`)과 bc5b510 Gateway·`deploy/cpu-lab/backend.json`·저장소 밖 임시 인증(principal `tenant-a-user`·`tenant-b-user`, role `inference`)/cpu-lab 값의 정책 1.2+`cache_isolation`/환경변수 HMAC 키로 공통 system prefix를 둔 약 940토큰의 같은 chat 요청(`max_tokens: 4`)을 순서대로 보냈다. 요청 전후 `vllm:prefix_cache_queries`/`vllm:prefix_cache_hits` 증가분은 `per_principal`에서 A `943/0` → B `943/0` → A `943/896` → B `943/896`, `off` 대조에서 A `943/0` → B `943/896`이었다. 6건 모두 200이고 Gateway 기록은 활성 4건 `applied: true`, 대조 2건 `applied: false`였다. 이 조건에서 B가 A의 직전 캐시를 쓰지 않고 각자 재요청에서 자기 캐시를 쓴 성질을 지표로 확인했을 뿐 보호를 증명한 것은 아니다. HMAC 키와 `tenant-a-user` salt 문자열은 Gateway data 디렉터리와 vLLM 로그 검색에서 발견되지 않았다. 증거 위치: **임시 scratchpad, 저장소 밖, 보존 안 됨**.
- 한계: CPU·요청 6건·단일 실행이고 응답 시간 차이(타이밍 신호)와 성능 비용은 직접 측정하지 않았다. GPU·SGLang은 미확인이고 GHSA-935w·2823 관련 수정은 v0.31.0 소스 대조만 했으며 실행 검증하지 않았다. Codex 읽기 전용 대조로 v0.31.0 설치본의 `cache_salt` 수용·128자/금지문자 검증·첫 블록 extra key 체인·prefix cache 지표가 HEAD와 기능상 같음을 확인했다.
- 후보 C(토큰 ID 범위 검사)는 만들지 않는다. Codex 읽기 전용 대조에서 v0.31.0이 `stop_token_ids`·`logit_bias`·`allowed_token_ids`·프롬프트 token ID를 `get_vocab_size()`(HF config `vocab_size`) 기준으로 직접 범위 검사함을 확인했고, Claude는 `sampling_params.py` 590~610행 부근에서 `temperature`의 finite·0~2와 `top_p`의 (0, 1] 검사를 확인했다. 기본 profile은 해당 필드를 거부하며, 필요한 회사는 후보 B의 validate 규칙에 0~vocab-1 범위를 직접 적어 열 수 있다.
- 후보 D(엔진 장애 원인 요청 추적)는 범위가 정해지지 않아 보류한다. 1회 장애 뒤 일시 차단, OOMKilled처럼 원인이 판명된 장애 제외, 평소 대비 희소한 모양만 대상으로 하는 오탐 감소안만 논의했다. **Claude 실행, Codex 교차 검수 전**.
- 2026-10-29 교차 검수 3건을 반영했다: engine FQDN 송신에만 DNS를 허용하고 빈 목록은 Cilium deny-all로 렌더, `perf_compare.py`에 소유자 전용 env 파일의 inference Bearer 인증 추가, 별도 `cache_mount_path`가 있으면 host cache root와 container 공급·manifest 경로 비교 생략. 후속으로 kind L-02를 Gateway DNS 허용·engine DNS 차단 판정으로 맞췄으며 kind 재실행 전이다. `TMPDIR=/dev/shm .venv/bin/python -B -m unittest discover -s tests`는 516건 중 408건 통과·108건 소켓 제한 등 skip·실패 0, AWS intent 임시 사본 render는 engine egress `[{}]`·DNS 없음. 실제 AWS/Cilium 적용은 미실행. **Codex 구현, Claude 검수 완료**(Claude 샌드박스 밖 unittest 516개 전부 통과, skip 0).

## 2026-11-01 AWS dev 인수 판정 보수

- AWS-03 최대 256토큰 응답을 GPU 속도 미측정 상태에서 5초 504로 오판하지 않도록 GPU LAB `relay_timeout_s`를 60초로 늘렸고, `stream_idle_timeout_s` 30초·`request_deadline_s` 300초는 유지했다. `kubectl kustomize`와 전용 unittest 33건은 통과했다. 요청된 기본 discover는 `tests/__init__.py`가 없어 0건(종료 5)이었고, 저장소 표준 `discover -s tests`는 526건 중 418건 통과·환경상 108건 skip·실패 0이었다. Claude가 샌드박스 밖에서 전체 unittest 526개 전부 통과(skip 0)를 확인했다. **Codex 구현·시험, Claude 검수 완료**
- 사용자 결정에 따라 AWS-01~08이 기능 연결을 근거 없이 통과하지 않도록 보수했다. stream SSE·상한값 허용/상한+1 엔진 로그 부재, TCP timeout errno와 정확한 Hubble dropped/허용 경로, `EROFS`만 RO 증거, 수집·Pod 교체 뒤 유한 재시도 창의 정상 추론 회복을 필수화했다. `ECONNREFUSED`, `EACCES`, `EPERM`과 증거 부재는 통과시키지 않는다. AWS-08 뒤 AWS-04·05 재실행 절차도 추가했다.
- 지적 전 시험: Codex 환경에서 `tests.test_aws_lab_acceptance` 32건 전부 통과, 전체 unittest 525건 중 417건 통과·108건 skip·실패 0이었다. Claude가 샌드박스 밖에서 지적 전 코드로 전체 unittest 525개 전부 통과(skip 0)를 확인했다.
- Claude 검수 지적에 따라 AWS-01의 `stream_ok`를 결정 항목에 추가했다. stream HTTP 200·SSE data/`[DONE]`·record `ALLOWED`·전달 시도 중 하나라도 명시적으로 어긋나면 실패하고, stream record를 읽지 못한 경우에는 `stream_ok: null`로 근거 부족(`unknown`)을 유지한다. 수정 후 Codex 환경에서 `tests.test_aws_lab_acceptance` 33건 전부 통과, 전체 unittest 526건 중 418건 통과·108건 skip·실패 0이었다. skip은 이 샌드박스의 local socket/socket I/O `Operation not permitted`로 인한 HTTP·socket 통합시험이다. 실제 AWS와 Cilium의 차단 errno/동작은 실행하지 않아 미확인이다. 수정 후 Claude가 샌드박스 밖에서 전체 unittest 526개 전부 통과(skip 0)를 확인했다. **Codex 구현·시험, Claude 검수 지적 반영·재검수 완료**

## 2026-11-01 AWS dev 1~3단계 실행과 runtime 보정

- **Claude 실행(1~3단계, Codex 교차 검수 전), 이번 수정은 Codex 구현·시험과 Claude 검수 완료(샌드박스 밖 unittest 526개 전부 통과, skip 0).** AWS dev에서 01의 `sudo -v`가 Ubuntu 이미지에서 실패해 서버 사본의 해당 1줄을 삭제했고, `unattended-upgrades` 잠금 해제 뒤 재실행했다. L4·드라이버 580.178.04·GPU 계산 `PASSED`, vLLM 0.31.0 일반/stream 응답 정상, 임시 접근 회수 후 시간 초과 차단을 확인했다. 엔진 digest `sha256:c1c9f6fd5c109ba7f0546a59f5b2f15fb87f64c77782e90a27b648b42a8e67c3`. 증거 `~/.local/state/serving-runtime/aws-lab/evidence/dev-20261101-stage1-3`; 실행 후 EC2 Stop.
- 묶음 엔진 args에 로그 옵션이 없어 `Received request`가 0건이고 AWS-01의 `engine_log_match` 및 의존하는 AWS-02·03 판정이 `unknown`이 되는 원인을 보정했다. 묶음의 engine-only 03 기준은 유지하고 runtime strategic patch에서 원본 args 15개 항목 뒤에 `--enable-log-requests`·`--enable-request-id-headers`를 추가했다. 03 재검증 회수 명령은 사이드카 삭제와 args 복원을 함께 수행한다. Tetragon `process.binary`와 집행 코드가 경로를 문자열로 비교하므로 intent의 세 단계 모두에 실제 `/usr/bin/python3.12`를 추가하고 기존 `/usr/bin/python3`도 유지했다. vLLM 0.31.0 설치본의 `request_logger.py`는 INFO에 ID·params, DEBUG에 prompt·토큰 ID·JSON 본문을 남길 수 있어 무해한 고정 시험 문장과 원시 엔진 로그 취급을 문서화했다. 인수 report에는 엔진 로그 원문을 복사하지 않는다.
- 로컬 `kubectl patch --local`로 묶음 템플릿과 통합 patch의 args 및 회수 후 원복을 확인했다. `tests.test_aws_lab_acceptance` 33건 통과, `tests.test_enforcement`·`tests.test_enforcement_v11` 53건 중 52건 통과·1건 skip, 전체 `discover -s tests` 526건 중 418건 통과·108건 skip·실패 0. `enforce render` 후보 10개와 `kubectl kustomize deploy/gpu-lab` 객체 7개 생성 성공. skip은 현재 샌드박스의 local socket/socket I/O 제한 등 기존 환경 사유다. 실제 AWS 재적용·AWS-01~03 재시험과 Claude 검수는 남았다. 커밋·푸시 안 함.

## 2026-11-01 AWS dev 4단계 결과와 후속 보정

- **Claude 실행(4단계, Codex 교차 검수 전).** Gateway 이미지 `rt-4fadbb3`로 AWS-01 단독 통과 뒤 AWS-01~03 묶음 통과를 확인했다. 상한 256토큰 요청은 200, 257토큰 요청은 403이었고 Gateway 요청 ID와 엔진 `Received request` 로그를 대조했다. 서버 Stop/Start 뒤 GPU 플러그인 복구 전에 생긴 `Failed`/`UnexpectedAdmissionError` Pod가 대상 목록에 남아 503 `TARGET_UNAVAILABLE`이 났으며 해당 실패 Pod 삭제 뒤 해제됐다. 이때 정책 묶음은 실제 intent의 `schema`와 bundle 검증의 `schema_version` 불일치 때문에 intent 없이 만들었다. 실행 후 EC2 Stop을 확인했다. 2차 보고서 `aws-acceptance-20261101T070029Z-7832`는 `pkill` 패턴이 원격 셸도 종료해 노트북으로 반출되지 않았고 서버 디스크에 남아 있으므로 다음 실행 때 파일 존재를 확인하고 회수해야 한다. 이 4단계 결과는 Codex가 AWS에서 재실행하지 않았다.
- **이번 수정은 Codex 구현·시험과 Claude 검수 완료(샌드박스 밖 unittest 530개 전부 통과, skip 0).** `enforcement_intent`만 실제 문서의 `schema`를 bundle 항목 `schema_version`과 비교하게 바꿨다. 실제 GPU lab intent의 서버별 값 사본으로 verify → tetragon feed가 있는 `mvp_options` → `load_intent`까지 통과하고, 불일치는 거부하는 시험을 추가했다. `gateway_policy`·`policy_approvals`·`exec_exceptions`·`target`은 기존 `schema_version` 비교를 유지한다. contracts의 bundle 항목 이름은 `schema_version`이며 platform API에는 이 역할의 실제 번들 생성 코드가 확인되지 않았다. contracts·platform은 수정하지 않았다.
- 대상 요약은 `Failed`·`Succeeded` Pod를 현재 후보에서 제외하고, 기존 `pods` 관측에는 phase와 함께 남긴다. Terminating은 기존처럼 제외한다. 실행 또는 대기 후보가 둘 이상이면 `multiple_targets`, 종료 Pod뿐이면 `not_found`를 유지한다. host `target-pods.json`은 phase를 이미 전달하므로 writer 변경 없이 Gateway 요약에 반영된다. AWS 인수 도구의 엔진 Pod 선택도 같은 기준으로 고쳤다. 이미지 digest 대조 방법, Stop/Start 뒤 GPU·Ready 확인, 서버 CLI 설치, 03 뒤 묶음 작성, 증거 파일 확인 뒤 Stop을 GPU lab 문서에 반영했다. 변경된 Gateway 코드를 AWS에 쓰려면 이미지 재빌드·재반입이 필요하다.
- 시험: `.venv/bin/python -B -m unittest tests.test_policy_bundle tests.test_mvp_target tests.test_aws_lab_acceptance` 70건 중 66건 통과·4건 skip, `tests.test_target_pod_snapshot` 12건 통과(실패 Pod를 host writer→Gateway collector로 전달하는 시험 포함), 전체 `discover -s tests` 530건 중 422건 통과·108건 skip·실패 0, `kubectl kustomize deploy/gpu-lab` 성공, `git diff --check` 성공. skip은 이 실행 환경의 local socket/socket I/O 제한 등 기존 사유이며 Claude의 샌드박스 밖 재시험과 실제 AWS 재적용은 남았다. 기준 `ae5be37`, 미커밋·미푸시. PR #177 상태는 `gh`의 GitHub API 연결 실패로 확인하지 못했다.

## 2026-11-02 GPU lab 정책 수동 적용 절차

- `deploy/gpu-lab/정책_수동적용_절차.md`에 플랫폼 승인 판본의 수동 변환·전달·적용, 검증, 되돌리기와 한계를 기록하고 README의 작성 주체를 첫 버전 기준으로 고쳤다. 근거는 `contracts/README.md`, `docs/결정기록_정책묶음_수신.md`, `mvp/policy_bundle.py`·`approval.py`, GPU lab 배포 파일이다.
- 저장소 밖 임시 폴더에서 GPU lab JSON 4개로 묶음을 만들어 `bundle verify` 성공, 정책 파일의 길이 같은 한 글자 변경은 SHA-256 불일치로 거부됨을 확인했다. `bundle.json.policy_ref`의 ID·판본이 Gateway 정책 파일과 달라도 로더가 통과시키는 결함을 재현했다. 런타임 코드는 고치지 않았다.
- 미확인: 플랫폼 승인 판본의 실제 변환·전달, AWS 서버 배치·재시작·정상/거부 요청. 다음 작업은 승인 판본과 서버 권한·경로를 확정한 뒤 절차를 AWS에서 실행하고 `policy_ref` 대조 결함을 별도 코드 작업으로 검토하는 것이다. 이번 작업은 커밋·푸시하지 않았다.

## 2026-11-02 AWS dev 시험 README 정리

- 2026-11-02 `README.md`의 AWS GPU 상태를 dev AWS-01~08 1회 통과로 바로잡고, 코드·판정·설치·실행 중 겪은 문제를 네 표로 정리했다. 상세 근거는 `deploy/gpu-lab/ACCEPTANCE.md`다.
- reference 정식 시험과 속도 측정은 미실행이며 dev 통과가 기능 완성을 뜻하지 않는다. 이번 문서 작업에서 코드·AWS 환경은 변경하지 않았고 커밋·푸시하지 않았다.

## 2026-11-02 AWS 인수 보고서 양식

- 다음 실행의 `report.md`에 시험별 실제 값·미확인 필수 항목·한국어 판정 기준과 한계를 표시하도록 수정했다. `report.json` 필드와 판정 로직은 유지하며 기존 보고서와 AWS 서버는 건드리지 않았다.
- 전용 unittest 37건 통과, 전체 542건 중 111건 환경상 skip·실패 0, `git diff --check` 통과. 합성 예시는 저장소 밖 임시 폴더에 만들었으며 실제 AWS 재실행은 하지 않았다.

## 2026-11-02 AWS GPU 추가 시험 준비 (기준 34f6395, 미커밋)

- Tetragon 미지원 `process_*` 이벤트를 손상과 분리해 `skipped_unsupported_total`로 누적한다. 기존 checkpoint의 손상 255는 수정하지 않는다. platform 상태 계약·예시·API·화면에 새 필드를 연결했다.
- AWS-09~11의 실행 코드·보고 기준과 가짜 보호 파일 ConfigMap/엔진 읽기 전용 patch, intent 의심 경로·보호 경로를 준비했다. `enforce render`의 TG-SECRET-01 후보 경로 일치와 `kubectl kustomize` 및 로컬 strategic patch의 기존 볼륨 보존을 확인했다. 실제 AWS 실행·적용은 미확인이다. 기존 AWS-01~08의 결과는 변경하지 않았다.
- 단계 판정은 Tetragon 원본의 namespace·Pod labels가 intent 엔진 selector에 맞는 실행으로 제한했다. 읽기 전용 원본 재생: exec 309건(엔진 272, 그 밖 37), exit 건너뜀 256, kprobe 2. 시간표 없음은 엔진 `phase_unknown` 272; 전 구간 합성 inferring 시간표는 엔진 `outside_allowlist` 221·`allowed_in_phase` 51이다. 실제 GPU 단계 경계가 아니며 상세 근거·정상 후보는 `docs/AWS_GPU_단계_사전분석.md`에 있다.
- 로그 수집 주체, 시간표 전달 경로, 열린 구간 만료 기준은 배포·계획에서 정해지지 않았다. `gateway.yaml`에 `--observation-timeline`을 추가하지 않았다. 권장 구조와 대안은 분석 문서에 적었고 선택 후 구현·AWS 검증이 필요하다.
- 시험: runtime 전체 546건 중 111건 skip·실패 0, platform API 변환 52건 통과, web 56건 통과·타입 검사 통과, `kubectl kustomize`·`enforce render`·로컬 strategic patch·`git diff --check` 통과. platform API `test_runtime_client.py`는 이 환경에서 60초 넘게 종료되지 않아 중단했다. 서버는 켜지 않았고 커밋·푸시하지 않았다.

## 2026-11-03 AWS-12 이미지와 AWS-13~15 Gateway 재연결 보정 (기준 887291e, 미커밋)

- AWS-12는 실행 중인 `other-client`의 `client` 이미지와 `imagePullPolicy`를 조회해 비교 Pod manifest의 두 placeholder에 치환한 뒤 stdin으로 적용한다. 이미지·pull 정책·실행 상태를 확인할 수 없으면 `unknown`으로 종료하고 비교 Pod를 만들지 않는다. 사용한 값은 보고서 증거에 남긴다. 비교 정책 판정 기준은 유지했다.
- AWS-13~15의 loopback Gateway 재시작 뒤 연결 오류가 나면 도구 소유 `kubectl port-forward`를 다시 열고 종료·예외 때 닫는다. 이미 사용자 포워드가 포트를 잡고 있으면 제한된 시험 구간에서 재시도한다. 비 loopback URL은 건드리지 않는다. 연결 오류는 Gateway 상태로 세지 않고, AWS-14에서 재연결 중 503을 놓치면 `unknown`으로 남긴다. 보고서에 복구 포워드와 재연결 시각을 기록한다. 별도 실행의 다음 사례 전에는 끊긴 사용자 포워드를 직접 다시 열어야 한다.
- 수정 전 회귀시험 2건을 추가한 전체 unittest 585건에서 그 2건이 각각 실패·오류로 재현됐다. 추가 회귀시험은 총 6건이며, 최종 `.venv/bin/python -B -m unittest discover -s tests`는 589건 중 476건 통과·113건 skip·실패 0이다. skip은 로컬 소켓 생성 제한 110건, 소켓 I/O 제한 3건이다. `git diff --check`도 통과했다. 서버 연결·실제 kubectl·실제 포트포워드·AWS 재실행은 하지 않았고 fake runner로만 확인했다. 기존 미커밋 변경은 보존했으며 커밋·푸시는 하지 않았다.

## 2026-11-03 SGLang v0.5.22 연결 준비 (기준 887291e, 미커밋)

- 사용자 지정 범위의 코드·가짜 시험·배포 초안을 추가했다. 포워드 복구는 도구 소유 프로세스가 살아 있으면 재시작하지 않고 새 포워드의 로컬 포트 준비를 최대 2초 기다린다. 수정 전 지연 포워드 시험 실패를 확인했고 수정 후 통과했다. 기존 미커밋 변경은 유지했다.
- SGLang 기본 조회를 `/ready`·`/v1/models`로 옮기고 본 모델의 `max_model_len`을 사용한다. `/server_info`는 자동 조회하지 않고 target은 version을 생략해 digest로 대조한다. `v0.5.22` 소스 근거와 미확인을 `docs/SGLang_v0.5.22_차이표.md`에 적었다. 가짜 SGLang은 `/ready`·`/v1/models`와 모든 도착 경로 관측을 제공한다. vLLM 기본 경로와 첫 모델 선택 규칙은 유지했다.
- `deploy/gpu-lab-sglang/`에 별도 engine·Gateway overlay·backend·target·intent·LAB 정책/승인 목록·단계 수집 없는 host service를 추가했다. UID 2000/GID 0, `/models` 읽기 전용, `/cache`, GPU 1, 8000, Secret 파일의 admin key, 레벨 0 JSON 요청 로그, 관리 수신부 허용 3경로를 명시했다. `SGLANG_전환절차.md`와 `ACCEPTANCE.md`에 순차 전환·vLLM 복귀·비파괴 확인·증거 절차를 적었다. AWS 설치 묶음 원본과 기본 vLLM kustomization은 바꾸지 않았다.
- AWS 도구에는 `--engine`(기본 vllm), SGLang JSON 로그 ID 대조, 두 엔진 고정 요청 목록 해시, 실제 Pod 인자·환경변수 이름·imageID 증거, stream 잘림 및 응답 usage 판정, 명시 선택 AWS-18(일반·stream·토큰화 경계/초과)·AWS-19(미지원 경로·필드)를 추가했다. AWS-11·16·17은 단계 근거가 없으면 `unknown`을 유지한다. AWS-18의 정확한 경계 프롬프트 탐색은 실제 환경에서 미확인이며, 토큰 계산 실패를 의도적으로 주입하는 사례는 가짜 엔진 시험으로만 확인했다.
- 검증: 가짜 runner·로그 표본과 target·경로·필드 단위 시험, SGLang YAML 구문 검사, `enforce render`(후보 10개) 통과. 실제 `kubectl kustomize`, 서버 접속, 실제 kubectl, 실제 SGLang 실행은 사용자 지시로 수행하지 않았다. 로컬 socket 제한으로 일부 HTTP 통합 시험은 skip되어 실제 엔진 결과를 증명하지 않는다. 전체 unittest와 `git diff --check` 최종 결과는 아래 인계에 별도로 기록한다. 커밋·푸시하지 않았다.

## 2026-11-03 AWS GPU 10회차 전 보정 (기준 e1c4b1b, 미커밋)

- AWS-13은 재시작 전 Gateway Pod UID를 기록하고 새 UID Pod의 종료·재시작·CrashLoop 및 그 Pod의 이전/현재 로그만 판정한다. AWS-17은 새 컨테이너 startedAt 이후 시작한 새 UID 시간표만 인정하며 이전 단계 행이 남으면 실패한다. AWS-07은 요청 직전 `/api/target`이 200·`unavailable`일 때만 중단 중 요청을 보낸다. `blocked`는 기대 200과 모순되어 실패, 조회 실패·기한 초과는 요청 없이 unknown으로 남긴다.
- backend 선택 필드 `context_headroom_tokens`(기본 0, SGLang 예시·배포 64)를 추가했다. `/api/status.engine_context_capacity`가 기존 `/v1/models` 수집의 `max_model_len`과 정책 상한+여유를 비교해 ok/insufficient/unknown 및 경고 코드만 표시한다. 플랫폼 contracts는 수정하지 않았다. SGLang 엔진 `--context-length`는 2112다.
- preflight `--boundary-probe`는 opt-in으로 Gateway 정책 상한을 읽고 엔진 Pod 내부의 `/tokenize`·chat/completions 경계를 직접 생성해 각 결과만 표시한다. AWS-18은 length 종료와 부족한 completion usage를 엔진 잘림으로 판정한다. 가짜 runner·회귀 시험과 `kubectl kustomize` 렌더를 확인했다. 실제 AWS 엔진 실측과 10회차 결과는 미확인이다.
## 2026-11-03 AWS GPU 1회차 후속: SGLang 단계·허용 목록 (기준 2f5f461, 미커밋)

- 근거: `docs/AWS_GPU_서버1회차_결과_20261103.md`, 외부 `sglang-observation/engine-sglang.log`의 키 없는 표지 5줄과 `tetragon-sglang.jsonl`의 `process.pod.container.name`·SGLang Pod UID 3개의 실행 경로. SGLang v0.5.22 GPU 캐시 있는 기동의 loading·compiling·inferring 표지를 추가하고 UTC `kubectl --timestamps` 우선 및 SGLang 연도 포함 로그 시각을 시험했다. 첫 기동 JIT 컴파일 위치는 미확인이고 `engine_signal`은 trusted가 아니다.
- SGLang 시간표 수집·preflight·AWS-11/16/17 판정 경로를 켰다. vLLM/SGLang intent에 관찰 경로와 엔진 컨테이너 이름을 반영하고 셸은 suspect로 유지했다. 컨테이너 이름이 없는 이벤트도 엔진 selector가 맞으면 판정하고 귀속은 `unavailable`로 표시한다. `cicc`의 관찰된 `bin/../nvvm` 원시 문자열만 SGLang 단계 목록에 명시하고 실행 경로는 정규화 없이 정확히 비교한다. SGLang 시작은 Secret의 `config.yaml`을 `--config`로 읽도록 바꿨으며, 이전 셸 방식 복구 절차를 문서화했다.
- 시험: 임시 설치 venv에서 `python -m unittest discover -s tests` 641건, 통과 526·skip 115·실패 0. skip은 로컬 socket 생성 제한 112건과 socket I/O 제한 3건이다. 두 intent의 `enforce render` 후보 각 10개 생성, SGLang `kubectl kustomize`와 `git diff --check` 통과. 새 fixture에는 표지 5줄만 있고 `server_args` 줄은 없다.
- 다음 작업: 서버 이미지 안 `--config` 구현·Secret 읽기·기동·첫 기동 JIT 단계·AWS-11/16/17을 실측한다. 변경된 intent를 포함하는 새 묶음을 만들고 `bundle verify` 및 후보 규칙 검토·적용이 필요하다. intent 해시/크기는 아래 교차 검수 수정에서 다시 바뀌었다. Gateway 정책 파일·정책 승인 목록·플랫폼 승인 판본은 수정하지 않았으며 재승인은 사람이 판단한다.

## 2026-11-03 교차 검수 A–D 후속 (기준 2f5f461 + 기존 미커밋 변경)

- 코드·시험 대조로 A–C가 타당함을 확인했다. 두 intent의 가변 실행 경로 `/proc/self/exe`·`/dev/fd/11`을 허용 목록에서 빼고 `/usr/bin/nvidia-ctk`는 유지했다. 실행 문자열 정규화를 제거했다. SGLang에서 관찰된 `cicc`의 `bin/../nvvm` 표기만 단계 목록에 원문 그대로 명시한다. `/cache` 시작 우회 문자열은 `exec_from_cache` 경고, 다른 우회 문자열은 활성 단계에서 `outside_allowlist`다. 첫 단계 표지 전 가변 경로는 `phase_unknown`이며 AWS-11 잘못된 경고 수에 들어가지 않는 회귀시험을 추가했다.
- Pod selector가 맞으면 컨테이너 이름 누락·형식 오류도 엔진으로 판정하고 귀속은 `unavailable`로 표시한다. 명시적으로 다른 컨테이너 이름은 제외한다. AWS-16/17은 vLLM·SGLang 모두 시간표 `extraction.engine`이 선택 엔진과 일치해야 판정한다.
- D는 11/3 서버 관찰 문서의 위험 표와 일치한다. `--config`는 `/proc` cmdline 노출과 시작 셸만 없앤다. 시작 로그 `server_args`와 키 없는 `/server_info`의 관리 키 노출은 그대로이며, 엔진 8000 직통 차단(AWS-04)과 증거 반출 전 값 검사·가림으로 다룬다. 외부 이슈 링크는 확인하지 않아 넣지 않았다.
- 로컬 검증: `.venv/bin/python -m unittest discover -s tests` 645건 통과(skip 115: 로컬 소켓 제약), `git diff --check` 통과. 실제 서버 적용과 AWS-11/16/17 재측정, 새 묶음 검증·정책 적용은 미확인이다. 다음은 서버에서 값 비노출 범위를 확인하고 새 intent로 시험을 재실행하는 것이다. intent SHA-256/크기: vLLM `0e347a98…`/2759바이트, SGLang `74753590…`/3430바이트. 커밋·푸시하지 않았다.

## 2026-11-03 실행 부모 조건 (기준 961c455, 미커밋)

- 두 AWS intent 1.1에 관측 실행 파일별 부모 제약을 추가했다. 부모 불일치·누락은 `outside_allowlist`; 제약 없는 실행 파일은 기존 판정을 유지한다. SGLang `suspect_parent_contexts`는 `nvcc`·`ninja`의 `/bin/sh`를 loading/compiling에서만 `suspect_context=compile_toolchain_parent`로 설명하며, 신뢰 단계에서도 `suspect_binary` 의심 경고를 유지한다. `containerd-shim` 셸도 의심 판정을 유지한다. 시작 Python 부모는 정상 기동과 시험용 exec를 구별할 수 없어 제약하지 않았다. SGLang의 미관측 정규 `cicc` 표기는 목록에서 제외했다. 관측 출처와 부모 누락의 AWS-11 한계는 `deploy/gpu-lab/ACCEPTANCE.md`에 기록했다.
- 재검수 수정 후 `.venv/bin/python -m unittest discover -s tests`: 647건, 실패 0·skip 115(로컬 소켓 제약). `git diff --check` 통과. 앞선 `.venv/bin/serving-runtime enforce render`에서는 두 AWS intent 각각 후보 10개를 생성했다. 실제 서버 적용·새 image의 부모 관계·커널 차단은 미확인이다. 기존 미추적 파일은 건드리지 않았고 커밋·푸시하지 않았다.

## 2026-11-03 엔진 응답 소켓 timeout 검수 후속 (기준 8d515ec, 미커밋)

- `engine_client.request`의 응답 읽기에서 닫힌 HTTP/1.0 소켓의 `settimeout` 오류를 허용하고 매 `read1` 뒤 기한을 확인한다. `relay`와 공통 `response_timeout` 도우미를 사용한다. 저장소의 `settimeout` 검색과 이번 diff 대조에서 새 호출은 이 두 경로뿐이며, 기존 `relay` 보호 처리를 공통 도우미로 이동했다. contracts는 수정하지 않았다.
- 소켓 없는 새 회귀시험은 닫힌 소켓의 정상 본문 및 공유/자체 기한 초과를 확인한다. `.venv/bin/python -B -m unittest tests.test_mvp_engine_client_deadlines tests.test_mvp_relay_deadlines`: 12건 통과. `.venv/bin/python -B -m unittest discover -s tests`: 662건, 실패·오류 0, skip 115(이 환경의 소켓 제약). `git diff --check` 통과. Claude의 노트북 소켓 허용 환경 전체 재시험과 실제 엔진 상태 확인은 남았다. 커밋·푸시하지 않았다.

## 2026-11-03 교차 검수 기한·AWS stream 판정 후속 (기준 8d515ec, 미커밋)

- 연결 수립은 기한 내 별도 작업으로 대기하고, 공통 감시가 요청 전송·헤더·본문 중 기한에 소켓을 끊는다. 연결이 늦게 끝나도 닫는다. relay 일반·chat/completions stream·토큰 계산에 적용했으며, 공유 기한 뒤 토큰 계산 timeout/OSError는 504 `ENGINE_DEADLINE`과 클라이언트 연결 닫기로 분류한다. 공유 기한 전 실패는 503을 유지한다.
- AWS-01·18 stream 성공은 `[DONE]`과 Gateway 최종 기록의 ALLOWED/completed/오류 없음 여부를 함께 확인하며, 기록 누락은 unknown이다. GPU-LAB README의 엔진 수집 스레드 설명을 실제 순차 처리와 지연 한계에 맞췄다. contracts는 수정하지 않았다.
- 소켓 없는 회귀시험을 추가해 헤더 분할 전송·본문 대기 중 기한 종료, 연결 수립 지연, 정상 감시 취소, 토큰 기한 오류, `[DONE]` 뒤 최종 실패를 확인했다. `.venv/bin/python -B -m unittest discover -s tests`: 672건, 실패·오류 0, skip 115(로컬 소켓 제약). Claude 노트북에서 전체 재시험과 실제 엔진 확인이 남았다. 커밋·푸시하지 않았다.

## 2026-11-04 플랫폼 단계 시간표 조회 (기준 byul_runtime 작업 트리, 미커밋)

- 근거: Gateway에 기존 ExecPolicy 시간표 파일의 읽기 전용 `/api/phase-timeline` 투영을 추가했다. 수집 상태·15초 freshness·단계/근거/추출 수준만 내보내며 Pod 이름·UID는 제외한다. 파일 누락의 로컬 시연은 `RUNTIME_DEMO=1`일 때 합성임을 표시한다. `TMPDIR=/dev/shm .venv/bin/python -B -m unittest discover -s tests`: 688건 통과, skip 115건(로컬 소켓 제약). 실제 관측 mount·인증을 건 GPU 환경의 응답은 미확인이다.

## 2026-11-04 사용자 화면 검수 후속 (기준 9fc5056 + 미커밋 변경)

- 근거: 예시 시간표는 기존 합성 Tetragon 관측 시각의 처음·끝에 맞춰 생성해 단계와 실행 점이 겹치며, 실제 시간표 파일 조회 분기는 그대로다. 로컬 전체 unittest 689건 통과(skip 115: 소켓 제약)와 단위시험으로 시간 포함·합성 표지를 확인했다. 실제 GPU 관측 mount와 화면에서의 점 배치는 미확인이다.
- 2026-11-05 Tetragon 선택적 차단 설계안 `docs/Tetragon_차단_설계.md` 작성: 보호 파일 읽기 Override를 1순위로 제안하고 intent·묶음·계약·화면·되돌리기·kind/AWS 시험과 미확인 위험을 정리했다. 구현·적용·실측은 하지 않았으며 계약 변경은 intent 1.1 선택 필드 범위에서는 불필요, 개별 사건 차단 판정 확장은 합의 필요다.
- 2026-11-05 Tetragon `TG-SECRET-01` 선택적 읽기 차단 후보와 intent 1.1 검증·단위 시험, L-05b kind opt-in 도구를 구현했다(기준 a9e487d, 미커밋); 전체 unittest 701건 통과(skip 115), `git diff --check` 통과, 실제 kind/AWS 차단·errno·복원은 미확인이고 Claude 검수 전이다.
- 2026-11-05 4회차 준비(기준 4432c0f, 미커밋): platform 웹 Override 차단 표지·알림 분리·기간별 개요 보조 표시, runtime AWS-20(승인 enforce 선적용·monitor 자동 복원), preflight 커널 수동 확인, 두 엔진 지표 이름 도구·확인순서를 추가했다. 웹 타입 검사·71건 및 runtime 708건(skip 115) 통과, `git diff --check` 통과; AWS 실측·커널 지원·지표·화면 실자료는 미확인이다.

## 2026-11-05 노트북 데모용 Tetragon Override 합성 관측 (기준 51f802e, 미커밋)

- `RUNTIME_DEMO=1` 실행에 한해 TG-SECRET-01 가짜 파일 읽기 Override 3건·Post 1건을 `--origin synthetic`으로 수집한다. 기존 관측 폴더에도 한 번 추가하고 데이터 폴더의 표지 파일로 재수집을 막는다. 실제 서버 관측·판정 코드는 변경하지 않았다.
- CLI 수집 후 정규화 필드와 웹의 합성·예시 및 차단 시도 표지를 시험했다. `TMPDIR=/dev/shm .venv/bin/python -B -m unittest discover -s tests`: 713건 통과(skip 115). `platform/web` 타입 검사·72건 시험, `git diff --check`, 셸 구문 검사 통과. 노트북 UI 수동 확인 및 실제 차단 결과는 미확인이다.

## 2026-11-06 차단 근거 판정·kind 실측·대시보드 연결 (기준 1a03e6c, 미커밋)

- 앞선 입력→runtime 묶음 연결·Tetragon 후보 구현은 PR #224로 머지됐다. 이번 변경은 파일 읽기 거부/주 프로세스 종료/서비스 복구를 분리 판정하고, 같은 정책·Pod·PID·가짜 경로·차단 시간·실제 Override 설정을 대조한다. 저장된 성공 표지도 Gateway에서 다시 판정한다.
- 새 `tools/tetragon_trial.py`로 독립 kind namespace의 HTTP 주 프로세스(PID 1)를 시험했다. 앞선 실측은 fd_install 종료137·재시작 복구, LSM의 read 권한 검사 -EPERM 및 종료·복구, Override 단독 EPERM을 확인했다. syscall은 실제 helper 미지원 load_error다. **최종 코드 재실행은 감시 읽기의 이벤트 연결 실패로 차단 성공을 재현하지 못했다.** 앞선 성공과 최신 미확인을 함께 보존하며 반복 재현/수집 안정성 확인이 남았다.
- 실측 중 Tetragon 1.7.1의 TotalMemlock/ListTracingPolicies 내부 오류로 에이전트 exit2·자동 재시작을 관측했다. 안정화 대기·에이전트 변경 감지·조회 실패 처리를 추가했지만 에이전트 버그 수정이나 감시 무공백 보장은 아니다. 최종 재실행 전후 restartCount 4 동일, 시험 namespace 정리 및 개발 수집기 재개 확인.
- 새 `tools/tetragon_status.py`는 노드에서 실제 모드·로드 상태·정책ID·기능 조건을 읽는다. Gateway `/api/tetragon-status` → platform `/api/runtime/tetragon-status` → `/runtime/enforce/tetragon` 화면에 전환/되돌림·실측 근거·에이전트 종료 이력을 제공한다. 개발 서비스에 연결해 fresh 실제 응답과 화면 HTTP200을 확인했다. 화면은 조회만 지원한다.
- 상세 근거: `docs/Tetragon_로컬검증_20261106.md`. 실제 보고서·원본·manifest·이벤트는 `~/.local/state/serving-runtime/tetragon-evidence/`에 보존한다. 이번 작업에서 AWS/GPU 실제 시험·원격 자동 적용은 하지 않았다.
- 시험: runtime 전체 741건 통과(skip 0), API 전체 197건 통과, web 타입 검사·91건 통과. 셸 구문 검사 통과. 기준 PR #224 머지 `1a03e6c`; 이번 변경 커밋·푸시하지 않았고 Claude 대화형 검수 및 브라우저 시각 검수 전이다. 기존 미추적 문서와 체크리스트 기록은 보존했다.

## 2026-11-06 귀속 누락 보완·반복 실측·로컬 커밋 (기준 1a03e6c)

- 고유 정책을 namespace 필터 없이 수집해 원시 이벤트의 process.flags=unknown·Pod 귀속 누락을 확인했다. procfs/PID namespace 불일치 경고도 확인했지만 에이전트 내부 원인을 단정하지 않는다. 임시 호스트 procfs mount·DaemonSet 경로 보정은 해결하지 못해 원복했고 계획된 에이전트 교체 2회 및 공백은 실측 성공 범위에서 제외했다.
- 시험 정책에만 current_task의 tgid·start_boottime·PID namespace 데이터를 추가했다. 파일·mask·훅·집행 action은 유지한다. Kubernetes/CRI가 식별한 정확한 컨테이너 init의 PID 1·namespace inode·시작 tick과 대조하고 차단 전 identity를 다시 확인한다. 독립 namespace의 고유 정책·같은 kernel PID·정책 ID·시간 구간을 요구하며 Gateway도 같은 근거로 재판정한다. 원시 Pod 필드와 독립 식별 결과는 identity_source로 구분한다. 일반 운영 process-cache 귀속 문제의 수정은 아니다.
- 새 실측: Sigkill 3/3(종료137·재시작/HTTP복구·monitor 복귀/읽기 성공; 파일 읽기 거부는 미확인), LSM Override+Sigkill 3/3(read 권한 검사 -EPERM 및 같은 종료/복구), Override 단독 1/1(사용자 공간 EPERM·Override·monitor 복귀), syscall 1회 load_error 미지원. 각 성공 실행의 전후 에이전트 UID·restartCount 0 동일. 근거는 `/tmp/byul-tetragon-repaired-*`와 `~/.local/state/serving-runtime/tetragon-evidence/`에 보존하며 이전 실패 기록도 유지한다.
- 새 파일: `enforcement/verification.py`, `mvp/tetragon_state.py`, `tools/tetragon_trial.py`, `tools/tetragon_status.py`, runtime 시험 2개, platform Tetragon 모델/API 시험/화면/화면 시험 및 `docs/Tetragon_로컬검증_20261106.md`. 기존 mode·AWS/kind 인수 도구와 Gateway 읽기 API·개발 연결·웹 타입을 수정했다. contracts·update 구현은 변경하지 않았다.
- 전체 검증: `TMPDIR=/dev/shm .venv/bin/python -B -m unittest discover -s tests` 742건 통과(skip 0); API의 runtime/update editable 설치 경로 전체 pytest 197건 통과 및 새 귀속 필드 투영 3건 재확인; web 타입 검사·91건 통과. 기존 Starlette deprecation/jsdom navigation/ResourceWarning은 있었고 실패는 없다. AWS/GPU·실제 엔진 503/복구·운영 수집 무공백은 미검증이다.
- 사용자 요청대로 로컬 커밋만 진행한다. 기존 미추적 인계·체크리스트·작업지시 문서와 외부 STATUS 기록은 커밋 대상에서 제외한다. Codex 자체 코드·시험 검수 후 Claude의 대화형 독립 검수는 별도다.
- Chromium에서 실제 개발 화면의 정책 4개·시험 결과 표·커널 task 식별 설명을 확인했다. LSM 성공 행의 근거 JSON을 실제 다운로드해 file_read_denial=confirmed·identity_source=kernel_task·CRI init PID 1을 대조했다. 페이지 오류 0. 개발 서비스 재연결 후 최신 실제 수집 상태를 확인했고 스크린샷/다운로드는 `/tmp/byul-tetragon-dashboard-*-reviewed.png`, `/tmp/byul-tetragon-reviewed-download.json`에 보존했다.
- 후속 검수에서 uint64 나노초를 문자열로 보존해 브라우저의 긴 uptime 정밀도 손실을 막았다. 상단 예시 경고는 실제 runtime까지 예시로 오해하지 않도록 예시 source에 한정했다. web 최종 타입 검사·92건(`--maxWorkers=1`) 통과. 동시 실행에서는 기존 웹 환경 폼 5초 timeout, 기존 runtime 관리 listener 50ms 한도 시험의 504/502 경합이 각각 1회 있었고 시간 한도를 완화하지 않고 재확인했다.
- 최종 순차 runtime 전체 재시험 742건 통과(skip 0, 140.208초); 관리 listener 실패 사례와 변경 기능 17건도 별도 통과했다. API 최종 전체 197건, web 최종 92건·타입 검사, 계약 13개 스키마/11개 예시 오류 0, 셸 구문 검사·diff 검사 통과. 원래 에이전트 spec·기준 정책 spec 일치, 잔여 시험 namespace 0, live 상태 fresh/ok·정책 4개·보고서 16개를 확인했다.


## 2026-11-06 운영 귀속·감시 중단/복구 보완 (기준 82804c1, 로컬 커밋)

- intent 1.1 선택 필드 `tetragon_kernel_identity`와 일반 운영 `enforcement/attribution.py`·`tools/tetragon_identities.py`를 추가했다. 적용 정책의 커널 tgid·group_leader.start_boottime·PID namespace와 정확한 CRI 컨테이너/Pod UID·procfs 시작 tick을 대조한다. 일반 ObservationFeed에 연결했고 같은 tick의 중복 후보·충돌·만료·너무 빠른 종료는 미확인으로 남긴다. 인자/환경 변수/파일 내용은 수집하지 않는다. 기존 trial 전용 귀속 코드와 구분한다.
- 새 `enforcement/recovery.py`·`tools/tetragon_stream.py`는 에이전트 UID/restarts/준비 상태와 스트림을 감독하고, 저장한 정책 spec·모드 기대값/수집기/식별 자료/복구 이후 새 실제 이벤트를 모두 요구한다. 살아 있는 writer의 이벤트 전달 중단도 감지한다. 기대값 자동 갱신·누락 정책 무조건 재적용·무공백/완전한 backfill 보장은 하지 않는다. 중복 종료 신호와 원격 자기 세션 정리도 보완했으며 최신 서비스 정상 재시작 1.63초를 확인했다.
- API/대시보드에 복구 조건 네 가지·공백 이력·운영 실측, 일반 이벤트의 Pod UID/컨테이너/커널·컨테이너 PID를 연결했다. 개발 스크립트가 생산자들을 명시 설정으로 함께 실행한다. 자가점검 DaemonSet은 가짜 파일만 읽고 노드당 receipt 한 건을 별도 저장해 보안 사건 수를 늘리지 않는다. 화면에서 서버 자동 적용/모드 변경 실행은 후속 범위로 유지했다.
- 실측 `byul-proof-4296fa7ca9`: 서로 다른 두 Pod의 주/자식 구분·에이전트 실제 교체·공백 감지·정책/새 이벤트 복구·원래 정책 보존·정리 통과. `byul-proof-265c9552ed`: group_leader 기반 스레드 접근까지 통과(재시작 시험은 이 실행에 포함하지 않음). 영구 보고서/원본/해시는 `~/.local/state/serving-runtime/tetragon-evidence/operations-artifacts/`에 있다. 개발 연결 전체의 에이전트 교체와 수집 child SIGSTOP→감지→SIGCONT 복구도 통과했고 `/tmp/byul-tetragon-live-agent-recovery.json`, `/tmp/byul-tetragon-silent-stall-check.json`에 기록했다.
- 현재 로컬 kind에는 기존 네 정책을 유지하고 별도 Post 정책 `runtime-tg-{asset,cache,secret}-01-identity-v2`·`runtime-tg-health-01-sentinel-v2`만 더했다. v1 추가 정책은 v2 새 이벤트 확인 후 제거했다. 실제 GPU 엔진/AWS/다중 노드 시험은 하지 않았다.
- 새 파일: attribution/recovery, identities/stream/operations_check 도구, runtime 시험 세 개, lab sentinel JSON, 운영 보완 문서. 수정: runtime intent/렌더/CLI/Gateway/feed/status, platform 상태 모델/생성 타입/관측·정책 화면/개발 실행 스크립트. contracts/update 구현과 기존 미추적 인계·작업지시·체크리스트 문서는 변경하지 않았다.
- 검증: API 전체 197건 통과. runtime 전체 766건 통과 뒤 추가 시험을 더했고, 767건 실행의 기존 정책 만료 시각 시험 1회 경합은 해당 사례 포함 25건 재시험 통과로 확인했다(시간 한도 수정 없음). 최신 runtime 전체 및 웹 타입/전체 시험은 마무리 실행 로그 `/tmp/byul-ops-runtime-final-verified.log`, `/tmp/byul-ops-web-verified.log`에 기록한다. 최종 결과는 아래에 덧붙인다.
- 사용자 요청으로 작업을 마무리하고 로컬 커밋한다. 푸시/PR은 하지 않는다. Claude 대화형 독립 검수와 최신 변경의 브라우저 시각 검수는 별도다. AWS 서버는 켜지 않았다.

- 최종 마무리 검증: runtime 전체 768건 통과(skip 0, `/tmp/byul-ops-runtime-final-verified.log`), web 타입 검사·93건 통과, API 전체 197건 통과, 계약 스키마 13개/예시 11개 오류 0, 셸 구문·`git diff --check` 통과. 잔여 `byul-proof-*` namespace 0, 실제 개발 상태 `ok/healthy`·정책 8개·운영 실측 보고서 2개를 확인했다.

## 2026-11-06 원격 적용·모드 전환 구현 (기준 b712bd5, 미커밋)

- 사용자 채택 D1~D7과 `docs/설계_원격적용_모드전환.md`에 따라 platform/api의 토큰 해시·역할·서버 제한, 추가 전용 감사 저장소, 운영 요청/승인/임대/다운로드/결과·agent-state API를 추가했다. 새 묶음·enforce는 2인 승인, LAB 자기 승인 명시 기록·양쪽 opt-in, bundle.switch/monitor는 즉시 대상이다. 요청 본문 역할은 무시한다. 기존 조회·내보내기·판정 동작과 contracts/update는 변경하지 않았다.
- runtime pull 에이전트는 파일 잠금·10초 조회·기본 dry-run·명시 execute, 묶음/승인/대상/기대 상태·해시 재검사, apply_bundle/기존 묶음 복귀와 tetragon_mode 전환·되돌림, 제한된 결과 보고를 수행한다. 임대 만료는 unknown·재실행 금지, 기대 상태 불일치는 failed STATE_CHANGED다. systemd·bundle apply만의 sudoers 예시를 제공했고 설치하지 않았다. 특권 결과 JSON과 이전 Gateway 재확인을 분리했다.
- intent 1.1의 규칙별 `tetragon_allowed_modes`를 추가했다. 기존 tetragon_rule_modes는 후보 능력 선언으로 호환하며 충돌을 거부한다. 새 enforce 허용은 명시 방식과 monitor 초기 렌더가 필요하다. 필드 미사용 렌더 바이트 호환 시험 통과. 현재 실제 MODE는 서버 조회/기록으로 관리한다.
- §6 조건은 같은 방식/spec/커널의 실제 재판정 근거·기존 available 능력·설정 모니터 기간/오탐 0 또는 전부 검토·수집 복구 4조건·enabled/허용 모드를 요구한다. 서버별 준비 조건을 화면/플랫폼에서 확인하고 실행 직전 live 커널·기능·정책·기대 상태와 다시 대조한다. 기존 unknown 능력은 승격하지 않는다. 모니터 구간 집계·검토는 신뢰 가능한 로컬 근거 파일로 제공해야 하며 자동 생산은 이번 구현에 없다. 기존 단일 daemon set-mode 제약으로 원격 enforce는 단일 노드만 허용한다.
- 화면에 정책 판본 [적용 요청], 운영 요청 목록/승인/단계/결과/증거 해시/에이전트 마지막 조회, Tetragon 체크리스트/차단·탐지 요청/Sigkill ENGINE_RESTART 입력을 추가했다. 토큰은 화면 메모리에만 보관한다. API/명령 예, 권한·승인 표, 안전장치 근거, 설치 예시, kind 명령·기대 증거와 변경 파일은 `docs/원격운영_사용과검증.md`. 공용 계약 후보는 사용자 지정 `docs/계약후보_operations.md`에만 두었다.
- 검증: runtime 전체 785건(669 통과·115 skip·기존 소켓 시험 PermissionError 1), 변경·묶음·모드 회귀 44건 전부 통과. web 최종 타입 검사·기본 npm test 98건 통과(첫 기존 환경 폼 timeout 1회 후 재확인, 단일 worker 98건도 통과). API 전체는 기본 uv 캐시 쓰기/DNS 제한을 확인한 뒤 캐시를 /tmp로 복사하고 offline+임시 epoll 20ms 시험 보조 설정에서 209 통과·기존 소켓 시험 PermissionError 1·HTTP 통합 skip 1. 새 API/로컬 통합은 13 통과·HTTP 변형 skip 1, 실제 export→승인→agent→실제 apply→Gateway 객체의 verified 확인. 전체 지정 명령의 완전 통과는 이 샌드박스에서 확인하지 못했다.
- 로그: `/tmp/byul-remote-runtime-final.log`, `/tmp/byul-remote-runtime-targeted.log`, `/tmp/byul-remote-web-final.log`, `/tmp/byul-remote-api-final.log`, `/tmp/byul-remote-api-operations-final.log`. sudoers `visudo -cf` parsed OK, `git diff --check` 통과. 원래 미추적 문서/STATUS 기록 보존, HEAD b712bd5 유지, 커밋·push·머지 없음. 실서버·GPU·kind·실제 HTTP Gateway·sudo 실행/서비스 설치·브라우저 수동 검수는 미실행이다. Claude의 소켓 허용 전체 시험·kind 실행·독립 검수가 필요하다.

## 2026-11-06 kind 지적 A·운영 화면 B 보완 (기준 b712bd5, 미커밋)

- A: 에이전트의 기본 상태 조회를 context/namespace/kubeconfig의 deployment/serving-gateway에 매번 새 port-forward를 여는 공통 재시도 경로로 수정했다. 고정 8080의 다른 Gateway를 읽지 않으며 --status-url은 명시 local 조회에만 쓴다. apply_bundle의 기존 새 포워드/되돌림 동작을 유지했다. 4역할 요구를 유지하고, Result의 선택 reason_code(BUNDLE_ROLES_MISSING/BUNDLE_INVALID/CURRENT_LINK_INVALID/STATUS_UNREACHABLE/STATUS_MISMATCH)로 비밀·경로 없이 실패를 구분한다. 기존 결과 코드·오래된 Result 본문을 유지한다.
- B: 운영 목록 우선·상태 필터·한국어 단계·요청/승인 역할 ID·시각·결과/세부 사유/증거 해시·행 상세·등록 서버별 마지막 조회를 구현했다. 토큰은 sessionStorage(실패 시 메모리)에 세션 유지하고 저장 후 입력에서 지우며 현재 역할/토큰 지우기를 표시한다. 정책 적용/모드 전환도 같은 토큰을 공유한다. 되돌리기는 버튼으로 열고 agent-state의 서버/current와 검증된 같은 대상 4역할 묶음 목록에서 선택하며 직접 입력은 고급에 뒀다. 새 상태 보고를 반영하고 정상 화면은 최근 30초 보고를 요구한다. 승인자/자기 요청/LAB 역할 처리를 API와 맞췄다.
- 내부 형식: agent-state의 선택 bundles(묶음 ID·SHA-256, 최대 256), 목록 actor와 미조회 등록 서버, claimed_at/running_at을 추가했다. 에이전트 상태 조회의 타 서버 정보도 제외한다. contracts/update는 수정하지 않았다. root 0600 토큰·kind 4역할 build 명령·대상 일치·노드 볼륨 준비와 에이전트 탈취 시 임의 root 적용 위험은 `docs/원격운영_사용과검증.md` §5/8/9/10 및 설계 위험 절에 기록했다.
- 검증: runtime 지정 전체 792건(676 통과·115 skip·기존 소켓 PermissionError 1), 에이전트/묶음 34건 전부 통과. web 최종 타입 검사·전체 109건 통과. API 지정 uv는 읽기 전용 기본 캐시 lock 생성 실패. /tmp 캐시·offline·기존 epoll 20ms 보조 설정 전체는 216 통과·1 skip·기존 소켓 PermissionError 1; 운영 API/실제 export→agent→apply→Gateway 객체 통합은 20 통과·실제 HTTP 변형 1 skip. 오류를 제품/시험 수정으로 우회하지 않았다. 소켓 허용 환경 전체 통과와 kind 재확인은 남았다.
- 로그: `/tmp/byul-remote-ab-runtime-all.log`, `-runtime-targeted.log`, `-web-final.log`, `-api-prescribed.log`, `-api-all.log`, `-api-targeted.log`(공통 접두사 `/tmp/byul-remote-ab`). 이번 추가 파일은 web의 `api/operationSession.ts`, `pages/RuntimeOperations.module.css`; 기존 미추적 agent/API/화면/시험과 인계/계약 후보 문서를 보완했다. `git diff --check` 통과, HEAD b712bd5·byul_runtime 유지, 기존 미커밋/미추적 작업 보존. 커밋·push·PR 없음. kind·실서버·GPU·sudo/서비스 설치·브라우저 시각 검수는 미실행이며 Claude 대화형 검수에 인계한다.
