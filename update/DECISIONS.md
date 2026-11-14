# DECISIONS

- 2026-11-14 · 원격 상태 재확인: 사용자 계정 dohashelby가 #264를06:16:06Z·#269를06:44:21Z에 병합한 사실을 API로 확인했다. 최신 main은512915e1이며 update/runtime CI 두 workflow가 성공했다. #269 head765def72와 다른 후속 커밋31ec6884의 SUP-08/TP2 실측 자료는 이 병합에 포함되지 않는다. 후속을 최신 main에 통합하고 별도 main PR로 검토한다. 기존 비용/코드 관문·M-B4 보관·runtime 무변경은 유지한다.

- 2026-11-14 · 사용자 결정34("V0 유지·해당 분기 UNKNOWN 예외 승인"): 지정 REQ-16 멀티모달 모델에서 기준 V0가 prefix caching을 내부 비활성화한 실제 source/로그 근거를 확인하고 V0 기본을 유지한다. 캐시 적중·격리의 측정력이 없는 해당 기준 분기만 원인을 기록한 UNKNOWN 예외로 승인한다. 전체 점검을 비적용/PASS로 만들지 않고 나머지 확인 가능한 분기를 계속한다. 기존 판정 기준·지정 모델·결정9-6의 양쪽 옵션 명시·다른 텍스트 모델/REQ-17 범위는 유지한다. 기준 V1 보조 실행·새 모델/다운로드·등록 승인은 포함하지 않는다. 실제 EVD/RES를 새로 생성하거나 전체 M-A6/7 완료로 세지 않는다. 열린 질문 M-A7-Q3을 닫는다.

- 2026-11-14 · 사용자 결정33(메인 세션으로 전달된 “GPU 연장에 필요한 시간과 비용 상한을 없애줘 비용상한만 100달러로 가자”): 결정32의 동일 서울 신규 g6.12xlarge1대와 부속 자원에 한해 고정2시간 제한을 해제하고 이미 발생한 비용을 포함한 누적 총상한을100달러로 변경했다. 기존 노드 수·다운로드32GB·검증 범위·정확 소스/CPU 자격·판정/계약 관문은 유지한다. 해당 노드의 예약 종료를 실제 확인한 뒤 취소하고, EC2/EBS/IPv4 누적 비용에20% 여유 및 전송/기타15달러를 예약해 보수 총액95달러에서 종료·정리하도록 로컬/노드 양쪽 감시를 둔다. 청구 확정액과 추산은 구분하며 작업 완료/유휴 시 조기 반출·종료·신규 EBS/SG 정리를 한다. 증설·별도 실증 캠페인·#269 병합·runtime 변경·M-B4 재개는 포함하지 않는다.

- 2026-11-14 · 사용자 결정32("신규 GPU 1대·2시간·총20달러 승인"): 서울 신규 g6.12xlarge1대(L4 4개 중2개 TP2), AMI ami-0d8ce11b83dd3fffc·암호화 gp3 200GiB를 최대2시간·EC2/EBS/공인IPv4/전송/세금/정리 총20달러 상한으로 생성·검증하도록 승인했다. 노드에서만 고정 이미지·기존 SUP-08 fixture·고정 REQ-16 모델·공식 설치 파일 합계32GB 이내를 받고 driver/모델 로드·실제 ENV/target·입력 사전 점검을 한다. TP2 장애/해제/복구는 CPU 직접 시험과 정확 소스 자격을 충족한 경로만 실행한다. 실패/상한 도달 때 중단하고 증거 반출 뒤 이번 신규 노드·새 EBS·전용 보안그룹을 삭제한다. 기존 자원·M-B4 보관·#263 draft·runtime 무변경·기존 판정/계약/고정값 관문을 유지하며 #269 병합은 제외한다. 실제 생성 전 단기 CLI 인증·AMI 공식 소유/사용 가능·자원 권한을 확인한다.

- 2026-11-14 · 사용자 결정31("로컬 레이어 다운로드·제한 추출 승인"): 작업지시서0.1절의 고정 GPU 이미지 두 개 중 기존 Python 두 레이어를 재사용하고 나머지46개 레이어를 로컬에 최대13,630,448,638 bytes 다운로드하도록 승인했다. 설치 소스/메타데이터 추출은 최대2GiB, 여유 공간12GiB 하한을 유지하고 레이어 크기/SHA·경로/링크/whiteout을 검증한다. GPU/클라우드 비용·컨테이너/엔진 실행·노드 기동·#269 병합은 포함하지 않는다. 실제 설치 bytes와 후보 고정값의 대조이며 GPU 등록·PASS로 확대하지 않는다. M-B4 보관과 고정값 관문은 유지한다.

- 2026-11-14 · 사용자 결정30(메인 세션으로 전달된 "#268 검토·병합 승인, #269 후보의 TP2 전체 검증·실제 GPU 자격 확인 / 실제 ENV/target 확보·연결, 노드 설치·자격·모델 로드 확인 / 최종 실행 입력 사전 점검"): #268의 CI18개 성공 head8f8c33a0을 검토하고 main03f70187에 병합했다. 병합 tree는 검증 tree와 같고 main의 update/runtime CI도 성공했다. #269의 전체 검증과 A7/A8 실제 입력 준비를 이어간다. 새 GPU/클라우드 과금·대용량 레이어/모델 다운로드는 구체 범위·비용을 따로 승인받으며 이번 로그인 완료 응답은 읽기 전용 자원 조회를 위한 것이다. #269 병합·runtime 예외 확대는 승인하지 않았다. #263 draft·#264 미병합·M-B4 보관·결정29의 원 경합 미해결을 유지한다.

- 2026-11-14 · 사용자 결정29("runtime 변경 없이 문제 기록"): #268 초기 headb086b195의 S2-05 수집 시계 경합은 재현 자료와 미적용 수정안을 보관하고 미해결 문제로 기록한다. `runtime/tests/test_mvp_acceptance.py`를 포함한 runtime 파일은 바꾸지 않는다. 시험 한 파일 예외 요청은 종료하며 기존 결정19/25의 승인 범위를 넓히지 않는다. 최신 head4d3192e4의 CI18개 통과는 이 경합의 해결 근거가 아니다. 실제 ENV/target·GPU 사전 점검의 미완료 상태도 유지한다.

- 2026-11-14 · 사용자 결정28(메인 세션으로 전달된 승인): CI를 재확인해 REQ-17 #267을 main에 병합하고 M-A7 `codex/ma7-input-readiness-20261114`(719ae2ca)의 push·main PR, M-A3 `codex/ma3-rank-fault-closure-20261114`(7997187b)의 push·main draft PR 생성을 승인한다. 최신 main 충돌은 변경을 보존해 정리하고 제품 소스 목록·결정5의 정확 SHA 검토 행을 다시 만든다. #267은 검증 head26f46c8e·CI18개 통과 뒤 main7181e57a에 병합했고 tree가 일치했다. 새 두 PR의 병합·GPU/클라우드 비용·추가 대용량 다운로드·runtime 예외 확대는 승인하지 않았다. #263 draft·#264 미병합·M-B4 보관은 유지한다.

- 2026-11-14 · 사용자 결정27("REQ-17 push·PR 생성 승인"): codex/req17-split-aggregation-20261114의53593d77 구현/입력 묶음을 push하고 main PR을 생성하도록 승인했다. 결정23의 기준200·AUC55·묶음512를 유지하며 병합·GPU/클라우드 실행·추가 다운로드는 포함하지 않는다. 필요한 CI 보수는 이 범위 안에서 진행한다.

- 2026-11-14 · 사용자 결정23("REQ-17도 설명한 분할 수집·집계 방식으로 구현, 기존 판정 기준과 묶음당512개 유지"): 504개씩 두 묶음의 원 표본을 동일 ENV/target·모델/정책/엔진 세대·요청 귀속에 결합해 집계한다. 역할별 최소200·AUC55·기존 단일 입력/512개 검사를 유지한다. 원 지연값에 기존 계산식을 적용하고 AUC 평균·기존 UNKNOWN 재작성·독립 FAIL 누락을 허용하지 않는다. 제한된 그룹 입력/원 표본 계약과 SPEC 반영을 승인하며 GPU/클라우드 실행·비용·추가 다운로드·새 브랜치 push/병합은 포함하지 않는다. 상세는 docs/dev-1/req17-split-collection.md. M-A7-Q1을 닫는다.

- 2026-11-14 · 사용자 결정26("#265·#266은 main에 병합"): 두 PR을 최신 CI18개 통과 head로 #265→#266 순서로 병합했다. main은 df1cf20c다. 소스 목록 재생성 결과와 기존 정확 검토 행이 같고 각 병합 tree가 검증 head tree와 같음을 확인했다. #263 draft·#264 미병합·M-B4 로컬 보관은 유지한다. 새 REQ-17 브랜치 push·PR/병합·GPU/클라우드 승인은 별도다.

- 2026-11-14 · 사용자 결정25("runtime 시험 세 파일만 예외 승인"): #266의 CI 차단을 고치기 위해 runtime/tests/test_downstream_deadline.py·test_mvp_auth.py·test_mvp_backend.py 세 시험 파일만 한정 예외 승인했다. final 저장 완료/요청 ID 결합을 기다리고 buffered slow-write의 시험 시계를 실제 쓰기부터 진행한다. 기존 assert·50ms 요청 기한·250ms 전체 상한·실제 소켓/Timer/저장·skip 호출을 보존한다. 저장소 밖 직접3개·준비 지연/final 저장 경합 재현 및 거부 진단9건을 확인한 구체 수정안에 대한 승인이다. runtime 제품 소스·정책·의존성·다른 파일·기한/기대값 완화·새 skip·GPU/클라우드·병합은 승인하지 않는다. CLAUDE.md11절의 일반 경계와 결정19의 기존 두 파일 예외는 유지한다.

- 2026-11-13 · 사용자 결정24("두 브랜치 push·PR 생성 승인"): M-A8 `codex/ma8-input-preflight-20261113`와 M-A7 `codex/ma7-materialized-inputs-20261113` 후속을 push하고 main 대상 PR 두 개를 생성하는 것을 승인했다. 병합·GPU/클라우드·추가 다운로드는 포함하지 않는다. M-B4는 로컬 보관하고 REQ-17 상한/정책 충돌은 미결로 유지한다.

- 2026-11-13 · 사용자 결정22("M-B4는 보관하고, M-A8 실제 입력·사전 점검과 M-A7 남은 입력 준비부터 진행"): M-B4 준비 브랜치는 로컬 보관하고 publish하지 않는다. M-A8의 실제 입력/사전 점검과 M-A7의 남은 입력 준비를 우선한다. 기존 단독 개발 결정은 바꾸지 않으며 GPU/클라우드·추가 다운로드·새 브랜치 push·병합 승인은 포함하지 않는다.

- 2026-11-13 · 사용자 결정21("push·PR만 승인"): M-B1 `codex/mb1-common-input-closure-20261113`의 시험·문서 후속을 push하고 main 대상 PR을 생성하는 것을 승인했다. CI 통과 뒤 병합은 이번 승인에 포함하지 않는다. GPU/클라우드·추가 다운로드·판정 의미·등록표 변경은 없다.

- 2026-11-13 · 사용자 후속 지시 18("현재 여유 공간 보고 나머지 작업 진행해줘"): 앞서 설명한 고정 가중치 8.29GB·로컬 fixture 재구성을 실제 여유 공간 확인 뒤 진행했다. 공식 고정 가중치 두 개(8,293,330,888 bytes)의 SHA-256을 검증하고 기존 도구로 같은 inventory·snapshot·코드 식별값의 새 fixture를 만들었다. 원본/파생 가중치는 하드링크로 한 번만 저장한다. 1GiB를 남기는 공간 확인에서 한 번 중단한 뒤 여유 증가를 확인해 부분 다운로드를 재개했으며 사용자 파일을 삭제하지 않았다. 로컬 기동 계획·정책/설정 준비까지이며 GPU/클라우드 실행·이미지 레이어 다운로드·다른 노드 기동·병합 승인이 아니다. 실제 ENV/target과 새 정본 결과는 미생성이다. 과거 원본 증거 복구로 세지 않는다.

- 2026-11-13 · 사용자 결정 17(원자료 회수): SUP-08 원자료 회수를 위해 기존 서울 EC2 `i-04f510eaa76c2bc89`를 필요한 경우 최대 20분 기동하고, 신규 EC2·전송 비용 합계 1달러 상한으로 작은 manifest·ENV/target·ZIP만 회수하도록 승인했다. 기동 전에 현재 요금·상태·기존 접속 수단을 확인한다. 전송 총량 10MB 이하, 엔진/GPU 시험·이미지/가중치 다운로드·새 리소스/권한 설정 변경은 제외한다. 직접 기동한 노드는 작업 후 20분 안에 중지하고 상태를 확인한다. 원본 존재는 미확인이며 과거 입력을 현재 실측으로 바꾸지 않는다. J01/J02·다른 노드 기동·병합 승인이 아니다. 승인 후 현재 로그인 계정의 서울 리전에서는 대상 인스턴스가 존재하지 않는다는 응답, 다른 노드에 연결된 EBS 세 개, 내 소유 스냅샷 0개를 확인했다. 기동하지 않았다. 다른 계정의 원본 또는 전달 ZIP이 없으면 새 fixture·새 실행 입력이 필요하며 과거 EVD/RES/RPT 원본을 복구한 것으로 세지 않는다.

- 2026-11-13 · 사용자 결정 9 항목별 승인: (9-1) LIF-13 공유 가중치 기능 부재는 기준 쪽 UNKNOWN. (9-2) LIF-05·06·16의 실행 중 전환 API 분기만 기준 UNKNOWN, Pod 교체 분기는 두 판본 실행. (9-3) LIF-07의 /ready·/readyz 준비 분기는 기준 UNKNOWN, /health 성공으로 대체하지 않음. (9-4) REQ-06 기존 대상 비적용을 유지하고 /v1/responses 부재 근거만 추가. (9-5) REQ-10 구조화 출력은 guided_json 대응, tool_choice=required만 기준 UNKNOWN. (9-6) REQ-16·17은 양쪽 --enable-prefix-caching 명시. 대응 데이터·계획 반영과 실제 소비 연결·GPU 검증 완료는 구분한다. 같은 답변에서 M-A6·M-A7 브랜치 push·PR 생성을 승인했으며 병합·GPU 비용·추가 이미지/모델 다운로드는 승인하지 않았다.


- 2026-11-13 · 사용자 결정 14: MVP 기준 판본은 기업 기본인 V0를 유지한다. V1로 바꿔 D1~D3를 줄이는 안은 적용하지 않는다. 결정 15: Python 설치 레이어 두 개(기준 약 427.6 MB, 후보 약 233.4 MB, 합계 약 661 MB)만 로컬 다운로드해 CPython 패치 판본을 확인하도록 승인했다. vLLM·의존성 설치 레이어 전체 다운로드와 GPU·클라우드 실행은 승인 범위가 아니다. 결정 9는 처음 항목별 검토로 보류했고, 별도 후속 결정에서 여섯 항목을 각각 승인했다.
- 2026-11-13 · 사용자 결정19("runtime 시험 두 파일만 예외 승인"): #257의 공통 CI 차단을 다루기 위해 runtime/tests/test_mvp_gateway.py와 runtime/tests/test_mvp_stream.py의 완료/시간 제어 수정만 예외 승인했다. final 저장 완료 뒤 기존 assert를 실행하고 stream 시작 뒤 제어 시계로 같은60ms 요청 기한을 검증한다. 기존 assert·60ms·STREAM_DEADLINE 기대값을 유지하고 HTTP504를 성공으로 받거나 skip/기한 완화하지 않는다. runtime 제품 소스·정책·의존성·다른 runtime 파일·GPU/클라우드·병합은 승인하지 않았다. update/CLAUDE.md11절의 일반 경계는 유지하며 이번 두 시험만 한정 예외다. · 응답 body 전송 뒤 final append라는 실제 순서와 stream 시작 전 예산 소진 가능성을 모의 재현으로 확인한 데 따른 것이다.

- 2026-11-13 · 사용자 결정20("#263은 draft로 두고, CI 통과한 나머지 PR들은 main에 병합한 뒤 남은 작업도 계속 진행"): #258·#259·#257·#260·#262·#261의 main 병합과 필요한 충돌 해소/소스 목록·검토 행 갱신·CI 확인을 승인했다. #263은 draft로 유지한다. 병합 순서는 #258→#259→#257→#260→#262→#261이며 후속은 앞선 통합 내용을 보존한다. GPU/클라우드 비용·추가 이미지 레이어·모델 가중치 다운로드·다른 runtime 파일 수정 승인은 포함하지 않는다. 비용 없이 독립적으로 할 수 있는 나머지 개발을 계속한다.

- 2026-11-13 · 사용자 지시("252 254 pr도 conflict 생겼는데 정리해서 병합해줘")로 결정 13(두 이미지 비교의 GPU 수락 조건)을 #254에 구현된 그대로 승인한다: 양쪽 GPU 모델·드라이버·GPU 수·TP 구성 일치, 양쪽 `gpu-bounded` 기동·할당 GPU 수 일치·CPU 프로필 없음, EVD 비교 가능성 정보의 gpu·driver가 ENV와 일치. 초안과 다른 두 점(성능은 수락 대상에서 제외, 수락 결과 형식 `guard-update-acceptance/2`)도 함께 승인한다. · 열린 질문 G1·M-A9-Q1을 닫는다.

- 2026-11-13 · 사용자 결정("이제 A/B 안나누고 혼자 할거야"): A·B 분담과 파일 소유 규칙을 없애고 한 사람이 update의 모든 작업(작업지시서 M-A*·M-B*)을 맡는다. 작업 번호와 과거 문서의 A·B 표기는 이력으로 둔다. 결정 5의 검토 기록 방식(B 검토 면제, `reviewed_by` 명시)은 그대로 쓰고, 작업지시서 결정 8(A 담당 재배정)은 불필요해졌다. `docs/phase1/dev-1.yaml`·`gpu.yaml`은 현황 도구 입력이라 유지한다. · 담당 간 대기·검토 왕복과 함께 쓰는 파일 조정 비용을 없애기 위함이다.

- 2026-11-12 · 사용자 결정 두 가지. (1) CPU용 vLLM 이미지(`vllm/vllm-openai-cpu`, `launch_profile=cpu-bounded`) 대상 검사·실증은 제품 범위에서 뺀다. 실제 엔진 실증은 GPU 이미지로만 하고, GPU 없는 개발·CI는 모의 엔진·고정 자료로 한다. (2) MVP 비교 범위는 "69개 모두 기준·후보 두 판본에서 실제로 관측해 비교"다. 기준 판본에 없는 관측 대상은 판본별 대상표(adapter 데이터)나 판본 profile로 고르는 adapter 관측기로 만든다. 다만 너무 오래 걸리거나 어려운 항목은 UNKNOWN으로 남겨도 된다. 그런 항목은 작업지시서 M-A6·M-B4 대조 보고에서 원인·영향 점검과 함께 제안하고 사용자가 항목별로 승인하며, PASS·NOT_APPLICABLE로 바꾸지 않는다. 일정·난이도 기준은 미리 숫자로 정하지 않는다. · CPU 이미지와 GPU 이미지는 digest·실행 경로가 달라 결과를 옮길 수 없어(CPU 결과는 GPU 실측으로 승격 불가) 같은 점검의 수집기·실증을 두 번 만들게 되고, 고정값 불일치를 그대로 공백으로 두면 오래된 기준 판본 쪽 비교가 대부분 비게 되기 때문이다. 이 결정으로 2026-10-24 "두 이미지 CPU 비교 수락 도구"의 CPU 전용 수락 조건(`update_validation.py`·`update_acceptance.py`의 `cpu-bounded`·`gpu_count == 0`)은 GPU 조건으로 바꿀 대상이 되고, 2026-10-26 지시의 "독립적인 CPU 구현·회귀"는 GPU 없는 개발(모의 엔진·고정 자료)로만 읽는다. `_cpu` 이름의 vLLM 소스 분석 실행기는 CPU 이미지와 무관해 제거 대상이 아니다.

- 2026-11-12 · 사용자 결정: MVP 판본 쌍을 기준 vLLM 0.7.0·후보 vLLM 0.30.0으로 한다(#246 PoC에서 이미지 고정). 11-09 결정의 0.31.0 실측·0.32.0 비교 기준을 대체하고, 0.31.0 고정값·지원표 행·기록은 데이터와 이력으로 둔다. 판본은 작업지시서 MVP 판본 표에만 적는다. MVP 동안 SUP-08은 A가 맡고(`docs/phase1/gpu.yaml`의 SUP-08 항목 포함), B는 SUP-08 전용 파일을 고치지 않는다. · 실제 회귀(SUP-08)로 `REGRESSED`를 확인하고, 판본 추가가 데이터만으로 되는지 MVP에서 함께 확인하기 위함이다.

- 2026-11-12 · 사용자 지시("B 배제하고 그냥 우리가 다해"): PR #240·#246은 B 검토 없이 A가 마무리한다. #246의 `reviewed_http_contract.py` B 고정값(`kubernetes_http.py`)과 최종 소스 목록 검토 기록은 A가 직접 넣고, 기록의 `reviewed_by`에 B 검토 면제를 밝힌다. 작업지시서의 일반 병합 규칙(B 검토 기록)은 바꾸지 않는다.
- 2026-11-12 · 사용자 결정 10(M-A6 판본 적용표의 (d) 제안): 기준 판본 쪽 D4(인증 middleware 내부 관측, 영향 PLT-10·11)·D5(V0 profiler 관측, 영향 LIF-14)는 원인을 기록한 UNKNOWN으로 승인한다. PASS·NOT_APPLICABLE로 바꾸지 않는다. D1(V0 scheduler·캐시)·D2(V0 model runner·CUDA Graph)·D3(V0 다중 프로세스·executor)는 UNKNOWN을 승인하지 않고 "(c) 후속, 미구현"으로 둔다. 미구현 동안 해당 점검의 기준 쪽은 `ENGINE_VERSION_UNSUPPORTED`이며 MVP 완료로 세지 않는다([적용표](docs/dev-1/ma6-version-applicability.md) 12절). 결정 9·기준 판본 V0 유지·이미지 레이어 다운로드는 미결이다. · 오래 걸리는 V0 재설계를 MVP 공백으로 굳히지 않고 후속 구현 대상으로 남기기 위함이다.
- 2026-11-12 · 사용자 결정 5(소스 검토 기록): MVP 동안 소스 검토 기록의 B 검토를 면제한다. 제품 소스를 바꾼 PR은 `tools/verify_req10_consumption.py --write`로 `release/reviewed-source.json`을 다시 만들고, A가 `tools/reviewed_manifest_receipts.json`에 검토 기록을 직접 넣으며 `reviewed_by`에 "B 검토 면제(사용자 결정 5, 2026-11-12)"를 밝힌다(#240·#246과 같은 방식). 나중에 병합하는 쪽이 목록과 기록을 다시 만든다. · B 검토 대기로 제품 소스 PR이 멈추지 않게 하면서, 면제 사실을 기록에 남기기 위함이다.

- 2026-11-11 · PR #240의 종료 기록·원자료·검토 JSON·미통합 후보는 커밋 `451292db`에 보존하고 docs의 중복 사본 144개를 정리한다. 현행 상태·잔여·담당·정확 복원 명령은 `docs/gpu-b/CURRENT_HANDOFF.md`에 둔다. 원 FAIL/UNKNOWN·Ref·출처와 로컬/AWS 원본은 유지하며, 후보 복원은 실행 승인이나 제품 완료가 아니다.

- 2026-11-01 · PACK31 원본은 reference/pack-seeds에 보존하고 현행 팩은 tools/regenerate_baseline_pack로 만든다. 복합 프로파일은 하위 수집별 세션을 따로 두고 결과만 모은다. · 파생 팩·과거 Ref를 현행 팩 변경과 분리하고 시간이 가까운 결과를 합치지 않기 위함이다.

- 2026-10-29 · process collector는 입력/정적 옵션을 처리하고 runtime 생성/프로그램 검증/포착은 기존 Kubernetes executor 경계에 둔다. · 기본 전체 시험에서 확인된 runtime 의존 아키텍처를 보존하고 같은 CLI/정책·출처·프로그램 bytes의 의미를 유지하기 위함이다.

- 2026-10-29 · A2 c4fdbe4의 PLT01/04 전용5파일은 원본 bytes로 통합하고 최소 공통 dispatcher/registry 및 최약 출처 제한만 추가한다. · 정상 대조 성공을 전체 PASS로 올리거나 lab=false/기본 runner로 EXAMPLE을 MEASURED로 승격하지 않으며 기존 container 사실/worker 접근 구분과 독립 FAIL을 보존하기 위함이다.

- 2026-10-29 · PLT08의 Kubernetes 한 구간 입력은 Docker 식별자 대신 정확 ENV/target/양쪽 binding hash와 명시 정책창을 결합하고 기존 정상 대조 source만 재사용한다. · 형식의 검증을 현재 PACK31의 정식 활성화로 오인하거나 Docker 필수값·기존 UNKNOWN/독립FAIL 의미를 우회하지 않기 위함이다.

- 2026-10-29 · PLT01/04 정상 대조는 기존 completions 경로/본문·고정 JSON 수집기를 유지하고 공백을 포함한 논리ID만 명시 wire ID로 정확 결합한다. · 요청을 chat으로 대체하거나 부분200을 성공으로 만들지 않고 기존 CheckDef/기능 판정과 REQ11·REQ10의 지원 범위를 보존하기 위함이다.

- 2026-10-29 · A2 자격수집 a0248fa는 전용3파일 원본 bytes로 공통 process 확정본 위에 통합한다. · 부분 scan의 앞선 환경 이름/독립 FAIL과 기존 ID 없는 callback을 보존하며 명시된 응답ID 불일치·완료 구간 오류로 환경 부재나 정상 대조를 확정하지 않기 위함이다.

- 2026-10-29 · PLT01/04 공통 process 입력은 고정 reader의 선택 컨테이너 보고와 capture 주체를 분리하고 worker/source/model 귀속은 별도 근거 없으면 false로 고정한다. · metadata·동일 UID·PID 목록을 실제 worker 전체/파일 소비/권한 PASS로 올리지 않으며 기존 전용 분석과 의미를 보존하기 위함이다.

- 2026-10-29 · REQ01/03은 기존 수집·안전 프로브·판정을 유지하고 선택한 네 Pod의 정확 세대/고정 HTTP-TCP 전송만 공통 경계로 연결한다. · Pod 이름/label을 외부대역·기업 principal·Gateway 변환표로 추정하지 않고 미지원 Service/TLS/host나 실제 실증으로 범위를 확대하지 않기 위함이다.

- 2026-10-29 · SUP03의 확정 전용2커밋/4파일 bytes를 보존하고 기존 registry와 같은 KubernetesHTTP에 등록한다. · template bytes/API 입력 projection과 실제 worker 소비를 구분하고 기존 UNKNOWN·정책·팩31·Docker 의미를 유지하며, CI 계정 외부 차단은 로컬/실제 근거와 별도로 미완료 처리하기 위함이다.

- 2026-10-29 · 순차 전환의 기준판 종료 대기만 require_ready=False/allow_terminating=True로 정확 소유 세대의 삭제 진행을 기다리고, terminal phase/부재를 보기 전에는 종료로 표시하지 않는다. · 정상 grace period와 교체/restart를 구분하면서 일반 HTTP/Ready/실증/worker-GPU 반납 관문을 유지하기 위함이다.

- 2026-10-29 · bootstrap 임시 접근 회수는 정확 소유 Ingress 타입/selector·UID/run을 확인한 뒤 API의 생략된 빈 ingress를 빈 목록과 같이 처리하고 실제 차단 대조를 별도로 수행한다. · Kubernetes의 빈 필드 직렬화 차이를 회수 실패로 오인하지 않으면서 null·남은 규칙·대상 변경은 승인하지 않기 위함이다.

- 2026-10-29 · A-2 inventory 안정성04353f4는 확정 전용3파일 bytes로 통합하고 stat/fstat 각 관측원의 안정성과 실제 열린 파일 신원을 구분한다. · 기존 모델 준비 제품 경로를 유지하며 metadata 변경 미관측을 불변 bytes·worker 소비·보안 완료로 확대하지 않기 위함이다.

- 2026-10-29 · container_config의 명시 Kubernetes 구현은 SUP04 API-only 소비부터 기존 제품 경로에 연결하고 다른 점검·HTTP/정상 추론·node 이미지 승인으로 자동 확장하지 않는다. · 확정 A-2 분석기와 기존 판정을 보존하며 미준비 runtime 관계와 부분 제품 연결의 UNKNOWN을 구분하기 위함이다.

- 2026-10-29 · 공동 base는 c9af729로 고정하고 A-1은 기존 Kubernetes 내부 snapshot 형태에서 검증된 소유 신원·mount 선언만 깊은 복사로 전달한다. · A-2의 기존 소비 의미와 lab/신뢰 gate를 보존하며 Pod API imageID를 node runtime·RepoDigest·manifest 계보 증명으로 승격하지 않기 위함이다.

- 2026-10-28: 공동 기준선은 main4af4d31·원문91e84c7에 기존 PR34 UPDATE 보수를 보존하고 PACK31/DRAFT로 구성하며, CheckDef는 두 계보의 최고 revision부터 최소 증가한다. 과거 팩·Ref는 고정 보존하고 A-1만 공통 파일·팩을 작성해 A-2 전용 구현과 충돌을 줄인다.

- 2026-10-27 · REQ-04는 기존 native/ASGI·소유 세션 경계에서 실제 helper 반환과 InputProcessor 필드를 관측하며 헤더 없는 호출도 별도 invocation으로 보존하고 profile5의 source23·지점·자산 bytes를 고정해 읽는다. · 운영 선택값·클라이언트 ID를 신뢰 신원/최종 worker 소비/기업 인가로 승격하지 않고 과거 기록의 의미를 보존하기 위함이다.

- 2026-10-27 · 기존 Kubernetes HTTP 전용 전송은 당시4381bytes 요청기를 고정 자산/SHA로 읽고 실제 http-probe/1을 기록하며 일반 HTTP/HTTPS 요청기의 profile2와 분리한다. · 동적 소스 재사용으로 공개 fixture 지문이 바뀌거나 새 HTTPS 동작이 HTTP 전용 계약에 소급되는 것을 막기 위함이다.

- 2026-10-27 · 일반 HTTP 요청기는 URL scheme별 표준 HTTP/HTTPS 전송과 기본 포트를 사용하고 TCP 연결 후 TLS 대기/검증 실패는 차단 timeout과 구분하며 평문 재시도 없이 실행 조건에 http-probe/2를 남긴다. · HTTPS를 평문으로 시험한 결과와 새 전송을 같은 비교 조건으로 취급하거나 TCP 도달/HTTP 성공을 TLS 배포·인가 전체 증명으로 확대하지 않기 위함이다.

- 2026-10-27 · REQ-05의 native 적재/호출 객체는 기존 센서 profile3의 약한 참조 지문으로 관측하고 profile1·2는 당시 source/지점 집합으로 읽는다. · 객체/설정과 가중치 신원·실제 소비를 구분하고 과거 계약 소급과 관측 때문에 모델을 계속 붙잡는 일을 막기 위함이다.

- 2026-10-27 · LIF-10 profile2는 같은 PID 문맥의 헤더 지문을 동기 native 할당에만 연결하고 profile1은 고정 과거 bytes로 읽기 호환한다. · ID 접두사 추측·장수명 handler 문맥 오인·헤더 재사용 병합·과거 기록의 의미 소급을 막기 위함이다.

- 2026-10-27 · LIF-10의 Kubernetes 읽기는 기존 Pod 세대 대조와 file/세션 파서를 재사용하고 API imageID를 고유 근거로 보존하며 node image/생산자 인증으로 승격하지 않는다. · Docker 신원을 위조하지 않고 Running 장애 자료·후행 확인 실패의 안전한 관측을 보존하며 기존 HTTP Ready 조건을 유지하기 위함이다.

- 2026-10-27 · LIF-10 세션 수집은 같은 소유 세대의 제한된 census·개별 기록과 지문 사건 참조를 보존하되 전체 실행 범위/귀속 인증/회수 판정으로 승격하지 않고 늦은 저장 실패 시 안전한 metadata를 표준 출력으로 반환한다. · 일시적 프로세스와 ID 재사용·클록 문맥·개별 실패를 숨기지 않고 기존 생산자 수집 경로를 재사용하기 위함이다.

- 2026-10-27 · LIF-10은 원래 native 정리를 대신하지 않는 source/code 검증 metadata 센서와 소유 파일 수집을 먼저 연결하고 단일 기록의 출처를 EXAMPLE/DECLARED 상한으로 둔다. · API 종료·finished ID·pool 반납·worker row와 생산자 인증을 혼동하지 않으며 기업 기한·전체 회수 판정을 새로 만들지 않기 위함이다.

- 2026-10-27 · OBS-09 새 첨부는 원문 native 파싱의 판정 표식 transcript와 별도 원문 지문만 저장하고 원문 파서 재실행 여부를 false로 명시한다. · 기존 판정·실제 요청·독립 FAIL·과거 HTML 재생을 보존하면서 원문 비저장과 재생 범위를 정확히 구분하기 위함이다.

- 2026-10-27 · OBS-03은 원문/저장 투영을 같은 strict 분석기로 대조하고 capture4/0.3.0의 재생을 PROJECTION_REPLAY_MATCH로 구별한다. · 원문 비저장·기존 판정 입력·과거 재생을 함께 유지하고 hash만으로 원문 재생·출처 인증을 주장하지 않기 위함이다.

- 2026-10-27 · OBS-09 참조 HTML의 인코딩/쓰기/소실도 공통 attempted/failed 첨부 경계로 처리한다. · 분석 뒤 파일 저장 실패가 독립 관측·성공 참조를 지우는 남은 경로를 닫되 정상 HTML·표시 검증·판정·비교 조건을 유지하기 위함이다.

- 2026-10-27 · 공통 실행기/판정기 예외 진단을 EXECUTOR_FAILURE/JUDGE_PLUGIN_FAILURE로 고정하고 늦은 판정 오류 뒤 앞축 PASS만 UNKNOWN으로 보류한다. · 동적 예외 원문·문자열화 부작용을 배제하고 기존 INV-15를 닫되 독립 FAIL·빈 ABORTED·취소 전파를 유지하기 위함이다.

- 2026-10-27 · REQ-13의 leaf 경로/반환 옵션/비공개 표식 판정은 원문으로 계산하고 신규 첨부·로그만 hash와 허용 metadata로 투영한다. · 자기 입력 인용과 실제 유출·독립 FAIL을 구분하는 기존 판정과 비교 조건을 유지하면서 원문 비저장을 지키기 위함이다.

- 2026-10-27 · 공통 수집기의 첨부 형식화/쓰기/확인 실패도 유효 관측과 성공 참조를 보존한 ABORTED로 남기며 Store 실패와 별도 고정 진단으로 구별한다. · 기존 첨부 보존 보수의 draft 이전 공백을 닫되 수집·설정·무효 draft 오류를 복구하거나 판정·출처를 강화하지 않기 위함이다.

- 2026-10-27 · REQ-11의 완전한 JSON/SSE 소비 분석은 메모리에 유지하고 저장 직전에만 원문 필드를 hash로 투영한다. · 스트림 시간·중첩·판정 의미를 바꾸지 않고 원문 비저장과 다른 연결의 독립 FAIL을 보존하기 위함이다.

- 2026-10-27 · REQ-07의 원문 증명 계산과 저장 투영을 분리하고 SUP-03을 포함한 기존 fixture hash는 원래 mask/정규화 식으로 유지한다. · 원문 비저장과 과거 비교 조건을 함께 지키며 기존 소비·거부·미수집 판정 의미를 바꾸지 않기 위함이다.

- 2026-10-27 · REQ-10의 두 전송은 REQ-09와 같은 저장 투영을 사용하고 control은 공통 단계에서 한 번만 hash한다. · 원문 비저장과 기존 Kubernetes 비교 조건을 함께 지키되 요청·판정·팩·스트림 시간 의미를 바꾸지 않기 위함이다.

- 2026-10-27 · REQ-09의 원문 검증은 메모리에 유지하고 신규 첨부는 input-response-projection/2의 hash·parse 상태·허용 메타데이터로 저장한다. · 기존 판정과 원인 수집 입력을 보존하면서 원문 요청/응답·token·preview·예외를 새 증거에 남기지 않기 위함이다.

- 2026-10-26 · SUP-13은 별도 byte 고정 supplied-template의 무해한 타입 참조 렌더링 한 분기만 기존 실행기에 연결하고 명시 POL 금지 참조 결과만 독립 FAIL로 사용한다. · 원문의 참조 실행 기준을 재사용하되 오류를 집행 증명으로 바꾸거나 기업 기본 금지·전체 sandbox PASS·worker 소비 인증을 만들지 않기 위함이다.

- 2026-10-26 · AWS 최초 인수는 고정 인계 bytes의 새 사본에서 원문 응답·Pod/로그 저장을 메모리 검증과 허용 메타데이터/hash로 바꾸고 소유 UID 조건부 회수만 사용한다. · 사용자 원문 보존 금지와 기존 자원 보호를 만족하면서 기존 정상/SSE·GPU 계산·거부 검사를 유지하기 위함이다.

- 2026-10-26 · Kubernetes 첫 연결은 기존 REQ-10 API 표본의 명시 context·namespace/Pod UID·containerd 세대·ENV image 대조와 전송만 별도 구현으로 재사용한다. · CPU 개발을 막는 전체 이식과 새 판정 의미를 추가하지 않고, 원문을 저장하지 않는 대표 제품 경로를 조기에 확인하기 위함이다.

- 2026-10-26 · PLT-08은 명시 소유 direct API 시험 hop의 금지 평문 정상 응답 분기만 기존 LIF-04 수집에서 투영한다. · 원문의 독립 위반 기준을 연결하되 배포 경로·TLS/peer 인증·전체 준수나 기업 기본 정책을 발명하지 않기 위함이다.

- 2026-10-26 · 첨부 IO 실패는 유효한 관측과 성공 첨부를 보존한 ABORTED EVD로 남기고 출처를 최대 DECLARED로 제한한다. · 기존 INV-04의 독립 FAIL을 지키면서 INV-02/11에 따라 수집 완결성과 PASS를 주장하지 않기 위함이다.

- 2026-10-26 · PLT-07은 명시 POL의 container UID+netns 모든 프로세스에 금지한 TCP 연결이 양쪽 소유 송수신 근거로 확인된 분기만 FAIL로 연결한다. · helper를 worker/SDK로 승격하거나 정책값·기본 금지·전체 PASS를 만들지 않고 기존 원문의 독립 위반 기준을 구현하기 위함이다.

- 2026-10-26 · LIF-04의 클라이언트 지연/close와 namespace에 묶인 aggregate 자원 관측을 제품 수집으로 제공하되 CheckDef 판정은 등록하지 않는다. · 연결별 buffer 귀속·worker 취소/회수·기업 예산을 수집 총량에서 추정하지 않기 위함이다.

- 2026-10-26 · OBS-03 strict JSON 분석은 새 capture/3·실행기0.2.1에 명시하고 과거 capture/2·0.2.0은 당시 분석 프로파일로 replay한다. · 모호한 사건으로 새 PASS를 만들지 않으며 저장 증거·과거 판정에 새 계약을 소급하지 않기 위함이다.

- 2026-10-26 · PLT-01의 부분 권한 목록과 전체 수집 여부를 분리하고 permission_scan_complete를 현행 정의의 PASS 관문으로 둔다. · 독립 위반은 보존하고 미수집 목록을 전체 정상으로 오인하지 않으며 과거 팩 계약을 소급하지 않기 위함이다.

- 2026-10-26 · REQ-05는 실제 Gateway 파일의 인가 metadata 수집만 제품 CLI에 연결하고 최종 모델 생산자 확보 전 executor/judge 등록을 보류한다. · 요청 모델·API 표시명·전달 시도가 실제 최종 모델 선택/사용을 증명하지 않으며 형식적 등록으로 자동화 수를 늘리지 않기 위함이다.

- 2026-10-26 · 센서 전달 오류는 원래 동작과 분리하고 고정 실패 상태를 내부 증거에 전달하되 SUP-03의 UNKNOWN 상한과 공용 계약은 유지한다. · 관측 오류가 정상 함수를 실패로 바꾸거나, 누락된 관측을 완전한 소비 증명으로 오인하는 것을 막기 위함이다.

- 2026-10-26 · 기존 점검 실제 재실증은 실행기·정의·설정·시험 자료·환경의 변경 영향을 확인해 해당 범위만 수행하며 신규 관측·미구현 점검을 우선한다. · 팩 revision 변경이나 과거 실측의 존재만으로 기존 다섯 점검의 두 이미지 시험을 반복하지 않기 위한 사용자 결정이다.
- 2026-10-26 · OBS-02/LIF-09 대신 PLT-02의 명시 Unix SOCK_STREAM·숫자 UID 연결 부분을 먼저 구현하고, 전체 IPC·명령 소비가 미검증이면 PASS를 만들지 않는다. · 현재 정상 기동 후 실행 골격에 worker 준비 실패의 독립 생산자가 없으며 IPC 커널 연결은 별도 관측할 수 있기 때문이다.
- 2026-10-26 · native 관측은 소유 파일/transport/해시와 생산자 주장 인증을 분리하고 REQ-02 미연결·SUP-03 UNKNOWN 상한을 유지한다. · 선택 자료 수집이나 source hash 일치가 실제 이미지 삽입·인가/worker 소비·출처 인증을 대신하지 않기 때문이다.

- 2026-10-26 · REQ-02는 신뢰 가능한 인가/rewrite/final-handler 수집원이 갖춰질 때까지 제품 연결·팩 변경을 보류하고 EXAMPLE 사슬 분석만 제공하며, SUP-03 소비 분석도 EXAMPLE 자료 일관성에 한정한다. · HTTP 응답·mount/API projection·합성 사건을 실제 소비나 보안 PASS/FAIL로 승격하지 않고 승인된 대체 범위를 구현하기 위함이다.
- 2026-10-26 · 점검별 실험 준비는 저장소 밖 새 폴더의 빈 DECLARED/DRAFT 정책과 미실행 사례 목록만 만들며 ENV·실행 설정·승인값·EVD/RES/RPT를 생성하지 않는다. · 기존 5개 수락 계약과 기업 입력의 소유권을 보존하고 준비와 실증을 구분하기 위함이다.

- 2026-10-26 · SUP-03은 readonly template 현재 bytes·소유/전후 신원·승인 목록 membership·엔진 API 입력 projection의 관측 전용으로 연결하고 보안·기능 PASS/FAIL 규칙을 두지 않는다. 과거 팩24 수동 계약은 보존하고 새 수동 PASS는 기존 bounded 지원 밖으로 보류한다. · 기동 시 loaded bytes와 독립 worker 소비가 미확인이므로 비승인 mount를 실제 소비 FAIL로 오인하지 않기 위함이다.
- 2026-10-26 · BYUL import 묶음은 원본 bytes·정규 해시·guard 봉인·정확 Ref·변환 내용·import 및 재검증 시각의 정책 유효성을 읽기 전용으로 확인하며 execution_ready는 null로 둔다. · 로컬 provenance는 진위 인증이 아니며 반입 일관성 검증을 엔진 기동 승인이나 이후 실행 시 자동 집행으로 오해하지 않기 위함이다.

- 2026-10-26 · SUP-04는 기존 container_config의 실행 이미지 승인 부분관측으로 연결하고 명시 승인 목록·소유 컨테이너·실제 image ID의 RepoDigest·전후 identity를 모두 확인할 때만 비승인 FAIL을 판정한다. 승인 일치의 전체 보안 PASS는 보류하며 정적 버전 0.5.0과 동적 fixture 이월 불가를 적용한다. · manifest와 local image ID의 혼동·수집 오류로 인한 FAIL 소실·과거 증거의 소급 해석을 막기 위함이다.

- 2026-10-25 · 사용자 승인으로 현행 개발·제품·CI·gua 시험 보조 Python을 3.12(`>=3.12,<3.13`)로 전환하고 기존 의존성 버전은 호환에 필요한 경우만 바꾼다. 별도 3.11용 제품은 유지하지 않고 과거 증거·팩·실행 환경 기록과 대상 vLLM 이미지 계약은 보존한다. · 지원 환경을 통일하면서 판정·해시·Ref·과거 재현 근거를 바꾸지 않기 위함이다.

- 2026-10-25 · U1 회귀 평가에 선택적 면별 부분 정답과 정확한 CHG/CAS 결합을 사용하고, 0분모·근거 부족·과거 PACK Ref 부재를 별도 한계로 표시한다. · rename 합집합이나 exit 0을 정확성 입증으로 오해하지 않으며 기존 판정·선정·이월 계약을 보존하기 위함이다.

- 2026-10-25 · git_diff 0.2.0의 artifact /2는 rename 양쪽의 분류를 보존하고 부분 미분류를 OTHER로 별도 기록한다. 기존 classified 집계와 과거 /1 저장본은 유지한다. · 한쪽 인식으로 미분류가 숨는 결함을 고치되 기존 보수적 선정·판정 계약과 과거 근거 해시를 보존하기 위함이다.

- 2026-10-24 · 모호한 REQ-09 길이 응답에는 명시적 추가 원인 관측을 별도 증거로 연결하고, PLT-04의 비어 있지 않은 무해한 후보는 동일 worker 주체의 메타데이터만 독립 시험한다. · 기존 판정/수락 기준과 과거 증거를 보존하며 source pin·실제 신원·모델·정상 대조를 확인하지 못하면 UNKNOWN이고 파일 본문·실제 자격 사용을 인증하지 않는다.

- 2026-10-24 · 사용자 승인으로 PR #29를 main에 병합하고 641f12f 위에서 REQ-09·PLT-04만 보완한다. · 새 자동화 확대 전 판정 의미·출처와 과거 계약을 보존한다.
- 2026-10-24 · REQ-09는 HTTP 성공·정상 생성만으로 소비/피해 FAIL을 확정하지 않고, 검토된 요청별 거부 근거가 없으면 UNKNOWN으로 둔다. · 상태코드와 실제 소비를 혼동한 오탐을 막고 확인된 위반·신고 FAIL은 보존한다.
- 2026-10-24 · 사용자 선택에 따라 forbidden_mount_paths는 명시 마운트 금지로 유지하고 선택 입력 credential_candidate_paths와 동일 worker 주체의 접근 권한 관측을 분리한다. · 기존 금지 위반을 없애지 않으며 후보 누락은 UNKNOWN, 명시 []는 후보 미지정의 부분 범위이고 자격 본문·내용 인증·실제 사용은 검증하지 않는다.
- 2026-10-24 · PACK22의 REQ-09 정의6·PLT-04 정의3에만 새 의미를 적용하고 판정표의 declared_pass_ceiling으로 수동 PASS 상한을 선언한다. · 다른 75개 정의·코어·저장소와 과거 팩21 재판정을 보존하고 판정기에 새 점검 ID 분기를 추가하지 않는다.

- 2026-10-24 · v1 마감 후보는 PR #24·#25·#28만 통합하고 #26 폼은 별도, #27 실패 이력은 문서 링크로 보존한다. · 새 기능 확대보다 의존 오류 보호·최종 회귀·현재 상태 정합성을 먼저 검증한다.
- 2026-10-24 · PR #28 대비 제품 차이가 수동 declared 예외 보호뿐이고 자동 다섯 점검 경로가 같다면 기존 실측을 통합판의 새 복원본에서 재판정한다. · 저장된 재판정 분석만으로 대체하지 않고 원래 as_of·저장 팩·원본 불변과 판정/비교를 확인하며, 새 엔진 실행으로 표시하지 않는다.

- 2026-10-24 · CPU 기동 준비 생성 1회와 전체 경로 합집합 fixture를 명시적 실행 옵션으로 분리한다. · REQ-01 본시험의 timeout·정상 대조와 INV-09의 동일 시험 자료 조건을 유지하고, 안전 probe는 소스로 확인한 버전·digest만 추가한다. 사용자는 60초를 넘겨도 시간 기록 후 계속하도록 지시했다.

## 2026-10-24 — 정책·로그 증거·설치 재현성 묶음

- 사용자의 직전 통합 프롬프트 실행 지시로 A~D 구현과 E/F 추가 작업, 전체 구현 후 테스트/수정·문서·커밋/일반 push/초안PR을 진행한다. 기존 runtime/판정/정의/CLI/CI 범위 변경은 이 지시로 승인되며 기업 값·승인·실제 엔진 시험은 포함하지 않는다. 새 의존성·객체·실행기 종류·범용 DSL을 만들지 않는다.
- BASE49526fb에서 codex/gua-policy-reliability-batch로 분기한다. 시작 시 PR20/19는 미병합, main24f7d90이다. 부모 PR을 병합/재지정하지 않고 새 초안을 쌓는다. 로컬 변경/원문/과거 팩/증거와 sources는 보존한다.
- REQ15는 M13의 false-only private_groups_server_overwrite 계약·정책3키·/3 generic 증거·순수 PASS 상한을 채택한다. 실제 자동 캐시 실행기가 아닌 제한 수동 계약이다. true·shared-service·안전 거부 방식·전체 신원/승인 인증은 지원 완료로 확대하지 않는다.
- 기존 로그 수집의 저장 후 크기 제한과 수신 중 제한을 구분한다. bounded stdout/stderr·시간/출처/원시bytes 의미와 조회 중 변경을 보강하고, 저장 캡처 분석은 원본을 고치지 않는다. legacy는 당시 관측 범위만 해석하고 새 수집 보장을 소급하지 않는다.
- 준비 guidance는 기존 ready/check_support/종료 코드의 의미를 보존하는 추가 정보다. 입력 독립 조회와 정책 의존 미검증, 총 자원과 여유 자원 미계측을 명시한다. 원시 예외/자격값을 안내에 복사하지 않는다.
- 테스트 속도는 테스트 전용 불변 seed/깊은 복사·순수 graph로 개선한다. 제품 팩 검증 cache·회귀 삭제·기존 skip/제외 증가는 금지한다. 구현 완료 뒤 기준 커밋과 같은 기존 집합을 비교하고 1분 달성을 미리 보장하지 않는다.

## 2026-10-24 — 두 이미지 CPU 비교 수락 도구

- 사용자 지시로 다음 개발 프롬프트의 구현 전체→테스트/수정→문서화·커밋·일반 push·초안 PR을 진행한다. 기존 5점검 한정이며 새 정책/실행기/의존성·7객체 변경·코어 판정 완화는 없다. 원격 PR19 미병합이므로 f4541ec에서 codex/gua-update-validation으로 분기하고 부모 브랜치에 초안을 쌓는다. 기존 PR 병합/재지정/rebase/force-push는 수행하지 않는다.
- 수락은 원본 RPT manifest의 10EVD/10RES와 CPU/모델/fixture/config·고정 판정표·정상 대조/챌린지·필수 관측·구체 보안/기능 판정·반출/복원·동일 저장 팩 재판정을 요구한다. 확인된 FAIL은 유효한 시험 결과이며 배포 승인과 구분한다. CPU 성능 UNKNOWN·팩/정책 승인 상태를 보존한다. MEASURED 라벨과 해시는 호스트 인증이 아니다.
- 같은 프로파일에서 이미지 digest만 다른 비교를 수락 대상으로 한다. 같은 버전의 다른 이미지도 version-upgrade와 구분한다. 프로파일 변경은 config hash 차이로 NOT_COMPARABLE이 맞으며 진단을 FIXED/REGRESSED로 꾸미지 않는다. 기존 후속 계획의 혼동 문장만 수정한다.
- 재판정 중 예외 만료 시각이 바뀌는 문제를 피하려고 update에 기존 judge/report와 같은 선택적 --as-of를 추가한다. 기본 현재 시각 동작은 유지한다. 도구는 한 시각을 기록해 세 명령에 전달하며 원본 store 대신 별도 replay-store를 쓴다.
- 현재 Docker는 가용하지만 로컬 vLLM 이미지는 한 종류라 실제 두 이미지 실행은 미수행이다. 이미지/모델 수신·기존 컨테이너 중지/삭제는 하지 않는다. 후보 버전·실제 ENV·자원 조율은 팀 결정이며 임의 후보 digest를 만들지 않는다. 코드/오프라인 검증은 이 환경 제약과 독립적으로 완료한다.

날짜 · 결정 · 이유 (한 줄씩). SPEC과 참고 문서가 충돌하면 SPEC을 따르고 여기 적는다.

## 결정
- 2026-10-23 · 실제 OBS-09 시도01에서 메모리 LF display digest와 Windows CRLF 첨부 바이트의 차이를 확인했다. 표시 내용과 첨부 Ref는 보존하고 고정 UTF-8 바이트 저장·회귀·새 OBS 단독02로 기록 digest=첨부 digest를 검증한다. 관련 없는 REQ-11/PLT-01 실측은 반복하지 않고 시도별 소스 SHA를 남긴다. · [직접 검증과 원본 보존 범위](docs/AUTOMATION_PHASE3.md#검증-기록).
- 2026-10-23 · 사용자 확인에 따라 자동화 3차는 REQ-11 직접 일반/SSE 두 연결 귀속과 OBS-09 실제 native INFO→guard 참조 수집 경로를 구현·최종 테스트·문서·커밋·푸시·초안 PR까지 수행한다. 새 실행기·declared 상한은 이 범위의 구현에 포함하며 코어·객체·저장소·의존성은 유지한다. · 실행 프롬프트.
- 2026-10-23 · 외부 request ID는 비신뢰 상관값이다. REQ-11은 실제 소비 prompt IDs/text와 원 연결을 대조하고, ID 충돌 자체·프로토콜 불완전·CPU 클라이언트 구간 중첩을 유출 또는 worker 병렬성으로 확대 해석하지 않는다. · 기업 principal/권한·취소·재시도는 별도.
- 2026-10-23 · OBS-09는 명시적 request-info-v1 고정 argv/env·실제 source SHA를 기록하며 임의 logging config를 받지 않는다. LF/CRLF를 보존한 CLI 디코딩 입력→순수 참조 parser→별도 JSON/escaped HTML 첨부를 검증하고 process/level/source 외 신뢰 필드를 만들어내지 않는다. · raw daemon·기업 SIEM·main RPT 상세 표시의 인증과 구분.
- 2026-10-23 · REQ-11의 두 문자열·OBS-09의 두 문자열과 필드 목록은 원래 복합 기업 정책의 제한 입력 투영이다. 비어 있지 않은 미지원 기업 값은 형태상 유효하나 수집/수동 PASS 상한에서 UNKNOWN이다. 317행 카탈로그 prefix와 과거 팩·실증은 보존한다. · 기업 기본값을 자동 채우지 않음.
- 2026-10-23 · REQ-07 첫 CPU 실증의 30초 cold 생성 timeout은 UNKNOWN으로 보존한다. template 실행기와 별도 chat 예제의 기술 관측 timeout만 상한/설정 120초로 보수하고 300초 새 송신 마감·남은 시간 제한·기업 POL·판정표·자원 상한은 유지한다. 기존 실행 안의 retry나 warmup 대체는 하지 않고 새 시도로 검증한다. · 첫 응답 30.095초 timeout, 후속 79ms 소비 일치의 실제 근거.
- 2026-10-23 · 자동화 2차는 사용자의 후속 구현·테스트·문서·커밋·푸시·초안 PR 계속 진행 지시에 따라 고정 CPU chat fixture, REQ-07 실행기와 declared 상한, 기존 REQ-13 private_system 분기, CPU 시간 제한 행렬 계약을 한 묶음으로 구현한다. 코어·객체·저장소는 유지하고 새 의존성은 추가하지 않는다. · 범위와 실행 프롬프트.
- 2026-10-23 · `guard-text-chat-v1`은 읽기 전용 별도 자산으로만 제공한다. 기존 모델 snapshot·기본 completion 경로는 보존한다. 실제 mount 바이트/이미지/argv와 소비 prompt IDs를 확인하며 요청 임의 Jinja·fallback·멀티모달·기업 template 승인 전체를 입증하지 않는다. · CPU fixture의 정상 요청과 정확한 소비 증거를 분리.
- 2026-10-23 · REQ-07 정책의 양의 입력 한도에는 임의 기업 상한을 두지 않는다. 실행 자원 한도와 분리하고 정확한 소비 최댓값·실제 시험 경계를 저장하여 다른 POL 재판정의 과거 true 플래그 재사용을 막는다. 허용 override=true는 유효한 입력이나 초기 deny 프로파일에서 UNKNOWN이다. · 수동 신고 PASS에도 같은 순수 검증·판정표 상한 적용.
- 2026-10-23 · 1차 CPU 행렬의 14/15·exit1은 보존한다. 짧은 HTTP timeout이 별도 Docker inspect/exec 시간 상한까지 보장하지 않으므로 3번째 사례를 강제 다섯 UNKNOWN 대신 관측 계약으로 평가한다. 과거 자료는 읽기 전용 분석 후 새 위치에 별도 결과를 남긴다. · [2차 계약과 검증](docs/AUTOMATION_PHASE2.md).
- 2026-10-19 · Python 3.11은 uv가 내려받은 인터프리터를 쓴다(사람 승인 "A"). 설치용 네트워크만 사용. · 로컬에 3.14뿐이었음.
- 2026-10-19 · 점검 행 ID는 첫 변환 때 배열 순서로 붙이고 `packs/PACK-BASELINE/id_map.yaml`에 고정한다. 재변환 시 질문 문구 → `subtest_ids`(유일할 때만) 순으로 기존 ID와 대조하고, 못 찾은 새 행은 영역의 다음 번호, 사라진 행은 `retired: true`. · SPEC §2.1 "한 번 붙인 ID는 바꾸지 않는다".
- 2026-10-19 · 자동화 행의 구조화 내용은 원본을 고치지 않고 `tools/overrides/<ID>.yaml`로 덧씌운다. · 재변환해도 구조화가 유지되도록.
- 2026-10-19 · `required_observations`는 SPEC 모양(`[{key,type}]` 목록)을 유지하고 원문은 `required_observations_prose`에 둔다. `applies_when`은 SPEC 모양대로 `prose`를 안에 둔다. · SPEC §3 예시 형태 보존.
- 2026-10-19 · `policy_params`는 구조화된 행만 채우고, 원문(`policy_to_decide`, `enterprise_decisions`)은 `policy_prose`에 보존한다.
- 2026-10-19 · 정책 `scope.include`가 비어 있지 않으면 include에 맞지 않는 점검도 `POLICY_SCOPE`로 미선정한다. · SPEC은 exclude만 명시. 사람 확인("진행").
- ~~2026-10-19 · REQ-01 관측 키는 SPEC 예시대로 `direct_route_status`, `gateway_route_status` 2개.~~ → 2026-10-19 5a에서 경로별 관측으로 대체(아래).
- 2026-10-19 · REQ-01 `executor: direct_route`. 실제 실행기는 5단계에서 만든다. 이번에는 `guard.toml`의 `[executors]`에서 `direct_route = "fake"`로 연결한다.
- 2026-10-19 · `rule_table`의 평면 `pass_when_all`/`fail_when_any`는 security 축이다. 다른 축은 `axes.<축>`에 같은 형식으로 둔다. 규칙이 없는 축은 UNKNOWN. · INV-18.
- 2026-10-19 · INV-11의 PASS 관문(정상 대조 성공·모든 챌린지 시도·필수 관측 수집)은 판정 플러그인과 별개로 judge 코어가 강제한다. FAIL 조건이 수집된 관측으로 성립하면 관문과 무관하게 FAIL을 유지한다. · INV-04.
- 2026-10-19 · 정책 예외의 만료 여부는 판정 시각(`--as-of`, 기본값 현재 시각)을 judge 코어가 받아 확인한다. 판정 플러그인은 시계에 접근하지 않는다. · INV-14.
- 2026-10-19 · FakeExecutor 증거와 예시 env/policy는 `data_origin: EXAMPLE`이다. · INV-02.
- 2026-10-19 · HTML은 jinja2 없이 표준 라이브러리로 렌더링한다. ULID도 표준 라이브러리로 직접 구현한다. · 의존성 추가 금지.
- 2026-10-19 · update 모드(2개 환경)의 적용 판단은 환경별로 계산해 더 넓은 쪽(APPLICABLE > CONFLICT > UNDETERMINED > NOT_APPLICABLE)을 쓴다. · 시험을 빼지 않는 방향.
- 2026-10-19 · 변경 사실이 관련 점검으로 가리키면 NOT_APPLICABLE이어도 선정한다(정책 scope 밖은 제외). 변경이 점검을 빼는 경우는 없다. · INV-05 "추가만".
- 2026-10-19 · CONFLICT도 inventory_complete에서는 "판단받지 않음"으로 본다. · INV-13을 보수적으로 적용.
- 2026-10-19 · 정책은 팩 Ref(id·revision·sha256)가 정확히 같아야 select에 쓸 수 있다. policy.yaml은 id·revision만 쓰고 로더가 해시를 채운다.
- 2026-10-19 · NOT_RUN·ABORTED EVD의 data_origin은 min(PLAN의 data_origin, DECLARED). 관측은 전부 `collected: false`, 챌린지는 `attempted: false`. · INV-02, INV-15.
- 2026-10-19 · run은 CheckDef.required_observations 키·challenge_steps만 EVD에 남기고, 실행기가 빠뜨린 것은 미수집·미시도로 채운다.
- 2026-10-19 · 같은 PLAN·점검·side를 다시 run/judge하면 같은 EVD/RES id의 새 revision을 만든다. 보고서는 최신 revision을 Ref로 고정한다.
- 2026-10-19 · 시연용 FakeAdapter(엔진 미기동)를 `adapters/fake.py`에 둔다. 실행기에 넘길 어댑터 자리를 채우는 용도.
- 2026-10-19 · 판정 플러그인은 PASS/FAIL/UNKNOWN만 낼 수 있다. NOT_APPLICABLE 등 다른 값은 UNKNOWN으로 바꾸고 사유를 남긴다. 적용 여부는 PLAN만 정한다. · INV-15.
- 2026-10-19 · 판정기가 없는 점검(76행)의 RES는 세 축 모두 UNKNOWN, 사유 "manual judgment required". · INV-10.
- 2026-10-19 · RES의 verification_level = weakest(실행 완료 시 EVD data_origin, 아니면 null; PLAN 조건들의 origin). · INV-12.
- 2026-10-19 · 보고서 집계에서 정책 범위 밖(POLICY_SCOPE) 점검은 빼고, NOT_APPLICABLE 점검은 NOT_APPLICABLE로 넣는다. 범위 밖만 있는 목적·영역은 집계 항목을 만들지 않는다. 범위 밖 개수는 limitations에 적는다.
- 2026-10-19 · update 모드의 목적·영역 집계는 후보(candidate) 기준. (8단계에서 재확인)
- 2026-10-19 · RPT에 `official`(정책 CONFIRMED·팩 REVIEWED·EXAMPLE 미사용일 때만 true)과 `policy_status`를 추가. 행별 `tested_while_undetermined`, `exception`(승인·유효한 예외만) 추가. · INV-17, §4.4 "적용 미정 상태로 시험함", INV-08.
- 2026-10-19 · 미충족 정렬의 review_order 순서: reference_baseline_first → implementation_path_first → conditional_when_selected.
- 2026-10-19 · 비교표의 한쪽이 NOT_APPLICABLE이면 NOT_COMPARABLE. · SPEC 표에 없는 칸.
- 2026-10-19 · `guard verify`를 저장소 일괄 검증으로 함께 만들었다(SPEC §9·§10). 승인 기록 명령(`approve`)은 만들지 않았다.

### 5a (2026-10-19)
- 2026-10-19 · REQ-01 관측을 경로별로 바꾼다: `routes_registered`, `routes_tested`, `direct_outcomes`, `gateway_outcomes`, `trusted_origin_reachable`. SPEC §3·§4.5·§6(INV-03 주석)·§7.2를 함께 고쳤다. · 원문 unknown 기준("/v1만 확인") 반영, 무응답 차단 PASS 표현.
- 2026-10-19 · `rule_table`에 연산자 `nonempty`, `all_in`, `any_in`, `any_between`, `all_between`, `superset_of`(+`value_obs`)와 규칙 전제 `when`을 추가. 코어에 REQ-01 분기 없음. · 최소 추가.
- 2026-10-19 · 등록 경로 = 엔진 `/openapi.json` ∪ Gateway 설정의 알려진 경로 − 정보성 경로(`/health`, `/ping`, `/version`, `/openapi.json`, `/docs`, `/redoc`, `/docs/oauth2-redirect`, `/metrics`). `/metrics` 노출은 OBS 영역에서 본다. · 사람 확인.
- 2026-10-19 · 직통 결과 분류: HTTP 상태코드 | `refused`(ECONNREFUSED) | `timeout` | `unreachable`(ENETUNREACH/EHOSTUNREACH) | `dns_error` | `error`. `refused`·`timeout`·`unreachable`은 양성 대조가 true일 때만 차단 근거. `dns_error`·`error`는 UNKNOWN. · 사람 확인.
- 2026-10-19 · 경로별 대표 직통 결과는 "키 없음"·"잘못된 키" 두 요청으로만 만든다. 순서: 2xx > 그 밖의 코드·dns_error·error > 401/403 > timeout/unreachable > refused. 출발점이 여럿(외부 컨테이너·호스트)이면 같은 순서로 합친다. 허용 키 직통 결과는 `direct_allowed_outcomes`로 증거 파일에만 남기고 판정에 쓰지 않는다. · 사람 확인.
- 2026-10-19 · 등록된 경로에서 직통 404는 UNKNOWN. · 사람 확인(열린 질문 5).
- 2026-10-19 · `gateway_outcomes`는 허용 키 Gateway 경유 응답. Gateway의 무키·오키 거부는 증거 파일에만 남긴다(REQ-01 판정 대상 아님).
- 2026-10-19 · Gateway는 nginx(`nginx:1.27-alpine`), 테스트 키는 실행 시 `secrets`로 임의 생성(허용/비허용 2개). 환경변수 이름만 docker에 넘기고(`-e NAME`) 값은 파일·로그·증거에 남기지 않는다. · 열린 질문 1.
- 2026-10-19 · 고정 모델 `facebook/opt-125m` + revision 고정(어댑터 인자). 이미지 digest는 실행 시 읽어 ENV에 기록. · 열린 질문 2.
- 2026-10-19 · 실험실 env는 DECLARED, `engine.name: lab-stub`. 실험실 정책은 **DRAFT**, `confirmed_by` 비움(실측 때 사람이 정함). · 사람 수정(원안 CONFIRMED → DRAFT).
- 2026-10-19 · 실험실(stub) 대상 실행 증거의 `data_origin`은 **EXAMPLE**. stub은 vLLM이 아니고 MEASURED면 official 보고서가 나올 수 있으므로. 실행기는 대상이 실험실이면(`target.lab: true`) EXAMPLE, 아니면 MEASURED로 표시. · 사람 결정.
- 2026-10-19 · 팩 검토자는 아직 없음. 팩 DRAFT 유지. · 열린 질문 4.
- 2026-10-19 · Docker 제어는 `docker` CLI를 `subprocess`로(`runtime/docker.py`). 모든 호출에 timeout 필수, 이름 접두어 `guard-<run_id>-`와 라벨 `guard.run=<run_id>`, 포트는 127.0.0.1에만, `DOCKER_HOST` 등은 코드에서 설정하지 않고 그대로 통과. · 열린 질문 6.
- 2026-10-19 · pytest 임시 폴더를 프로젝트 안 `.pytest_tmp/`로(`--basetemp`). · "폴더 밖에 파일 만들지 않기".
- 2026-10-19 · 연결은 됐는데 응답 전에 끊기거나 시간이 초과되면 `error`(phase after_connect)로 분류한다. 대상 포트에 도달한 것이므로 차단 근거(`timeout`)가 아니다. `timeout`은 연결 단계 시간 초과만.
- 2026-10-19 · 상태를 바꾸는 관리 경로(`/sleep`, `/wake_up`, `/reset_prefix_cache`, `/load_lora_adapter` 등, `adapters/vllm.py` MUTATING_ADMIN_ROUTES)에는 기본적으로 직통 요청을 보내지 않는다. 그 경로는 `routes_tested`에서 빠져 REQ-01은 PASS가 될 수 없다(UNKNOWN). `allow_mutating_routes = true`일 때만 보낸다. · 엔진 손상 방지, 정직한 미시험 표시.
- 2026-10-19 · SPEC §7.1 `EngineAdapter`에 `routes_from_openapi(doc)`와 `probe_request(route, model)`을 추가(기존 메서드 유지). 실행기가 출발점별로 받아온 openapi 문서를 어댑터가 해석하고, 무해한 요청 모양(`max_tokens: 1`)도 어댑터가 정한다. · 엔진 지식은 어댑터에만.
- 2026-10-19 · 실행기 레지스트리 키는 `<CheckDef.executor>:<구현>`(예: `direct_route:real`)을 먼저, 없으면 `<구현>`(예: `fake`). guard.toml `[executors] direct_route = "real" | "fake"`.
- 2026-10-19 · 직통 출발점: 외부 호출자 컨테이너(엔진 네트워크 밖, isolated) 또는 같은 네트워크(exposed), 그리고 엔진 포트가 127.0.0.1에 공개된 경우에만 호스트. 직통 대상은 엔진 IP(이름 해석 실패를 차단으로 오인하지 않도록).
- 2026-10-19 · 실험실 Gateway는 클라이언트의 Authorization 헤더를 지우고 엔진으로 넘기며, nginx 로그 형식에 헤더를 넣지 않는다.

### 5b (2026-10-19, 맥 CPU 경로)
- 2026-10-19 · 5b는 시험 서버가 아니라 **맥(Apple M5, macOS 26.6.2)의 Docker Desktop(VM: CPU 10, 메모리 7.75GiB, aarch64)에서 CPU 이미지로** 실행. GPU 이미지(`vllm/vllm-openai`)는 쓰지 않음. · 사람 결정(선택지 2).
- 2026-10-19 · 엔진 이미지 `vllm/vllm-openai-cpu:v0.31.0` → `vllm/vllm-openai-cpu@sha256:495c84fe30f3572b83a78100a1e8ba423dbb77fc532ecefebe114e6b77327bd9`(RepoDigests, 멀티 아키텍처 인덱스 digest. arm64 매니페스트는 `sha256:045fb648a61c…`). guard.toml `[vllm].image`와 ENV `engine.image_digest`에 기록.
- 2026-10-19 · **소스 commit 일치 확인 방법과 결과**: 일회용 컨테이너(`--network none`)에서 `vllm.__version__ = 0.31.0`, `vllm._version.commit_id = "gdb9527a46"`(setuptools_scm 형식: g + 축약 hash 9자리), 배포 메타데이터 `0.31.0+cpu`, `direct_url.json` = 로컬 wheel 경로(commit 없음), 이미지 라벨에 commit 없음, `/vllm-workspace`에 `.git` 없음. → 축약 hash `db9527a46`이 고정 commit `db9527a46873454610df6dbedf79a36d6bf1a7f6`의 앞 9자리와 일치, **40자리 전체는 확인 불가**. ENV `engine.source_commit_status: ABBREVIATED_MATCH`, `source_commit: db9527a46`, 보고서 limitations에 "축약 hash만 일치, 전체 commit 미확인"으로 표시. 일치로 단정하지 않는다.
- 2026-10-19 · 모델 `facebook/opt-125m` revision `27dcfa74d334bc871f3234de431e71c6eeba5dd6`: HF에서 공개·비게이트로 확인 후 vLLM 이미지의 `huggingface_hub`로 일회용 컨테이너에서 `~/guard-models`(HF cache)에 받음(토큰 없음, 388초). 이후 시험은 `HF_HUB_OFFLINE=1` + 읽기 전용 마운트.
- 2026-10-19 · (아래 "inventory 정의"로 범위 갱신됨) `tools/model_inventory.py`: snapshot 디렉토리 기준, 심볼릭 링크를 따라 실제 내용 해시, `[{path, bytes, sha256}]` 경로순 → 정렬 JSON → SHA-256. 결과 **파일 12개, 753,111,181 bytes, inventory_sha256 `d677f3999b13aab7bf9a7d203568ca63bc2d3fdc6a102b5efc2c719fd816ae6e`**. LFS 3개 파일의 sha256은 HF blob 이름과 일치. BoBProject 보고서의 37개와 **다르다**: 우리 cache 트리는 일반 파일 34개(blob·lock·xet 로그 포함) + 심볼릭 링크 15개로 37과도 맞지 않는다. BoB가 무엇을 셌는지(트리 전체? 다른 revision·allow_patterns?) 확인 필요(열린 질문).
- 2026-10-19 · ENV에 선택 필드 `runtime.platform`("CPU, arm64, Docker Desktop on macOS")과 `engine.source_commit_status`·`expected_source_commit` 추가. 비어 있으면 직렬화에서 빠지게 해 기존 객체 해시를 유지(기존 `.guard/` `guard verify` OK). 보고서 limitations에 실행 환경과 commit 확인 결과를 적는다.
- 2026-10-19 · `adapters/vllm.py` `launch_plan`에 `device: gpu|cpu`(gpu → `--gpus all`), CPU면 `VLLM_CPU_KVCACHE_SPACE`(기본 1GiB), `--shm-size 1g`. 엔진 인자는 계속 `start_command`만 만든다(`--model --revision --served-model-name --host 0.0.0.0 --port 8000`, 컨테이너 안 포트, `--api-key` 없음, `VLLM_SERVER_DEV_MODE` 미설정).
- 2026-10-19 · target.json `lab` 플래그: stub 엔진이면 true(→ EXAMPLE), 실제 vLLM이면 false(→ 증거 MEASURED). ENV는 DECLARED. RES `verification_level`은 증거와 적용 조건 출처 중 가장 약한 것이라 **DECLARED**(gateway 사실이 사람이 쓴 값). 5b engine 테스트의 "verification_level == MEASURED" 단언은 제 테스트 오류였고 EVD `data_origin == MEASURED` + RES `DECLARED`로 바로잡았다(판정 규칙 변경 아님).
- 2026-10-19 · direct_route 실행기는 target `engine.ready_timeout`(상한, 기본 600초)까지 신뢰 출발점에서 `/health` 200을 기다리고, 엔진 컨테이너가 죽거나 시간이 넘으면 예외 → ABORTED(INV-15).

**제안(적용하지 않음, 사람 확인 대기)** — 5b에서 실측한 v0.31.0 CPU 등록 경로(개발 모드 꺼짐) 22개 기준:
- 정보성 제외 목록과의 어긋남: `GET /load`(서버 부하 지표), `GET /kv_event_sources`(KV 이벤트 소스 정보)는 정보성·관측 성격이라 INFO에 넣는 것을 제안. `POST /is_scaling_elastic_ep`는 읽기 전용 상태 조회(현재 admin, 비상태변경으로 직통 요청함, exposed에서 200).
- `POST /v1/responses/{response_id}/cancel`은 사용자 객체 상태를 바꾸는 경로라 mutating 분류를 제안(이번엔 리터럴 `{response_id}`로 보내 404, 실제 객체 영향 없음).
- 경로 템플릿(`{response_id}`)은 존재하지 않는 고정 ID로 치환해 보내는 것을 제안(지금은 리터럴 그대로 → 404 → 해당 경로는 판정상 "그 밖의 코드").
- 기능 축: opt-125m은 chat template이 없어 `/v1/chat/completions` 400, `/v1/messages`·`/v1/responses`·`/generative_scoring`은 `{}` 본문이라 400 → Gateway 허용 키도 200이 아니어서 functional은 UNKNOWN. 경로별 무해 요청 본문을 보강하거나 정상 대조 경로를 추론 경로로 좁히는 것을 제안.

### 5b 후속: TCP 연결 단계 추론 (2026-10-19, 사람 승인)
- 2026-10-19 · isolated UNKNOWN(등록된 상태 변경 경로 `/scale_elastic_ep` 미시험)을 **판정 규칙을 그대로 두고** 증거 수집 방식으로 푼다: 직통 (출발점·대상, 키) 조합마다 먼저 TCP 연결만 시도(바이트 미전송). 연결 자체가 timeout/refused/unreachable이면 HTTP를 보내지 않고 그 조합의 모든 등록 경로(상태 변경 포함) 결과를 그 값으로 기록, 증거 파일에 `basis: tcp_connect (inferred)`. 연결되면 상태 변경 경로에는 계속 보내지 않는다(`allow_mutating_routes=false` 유지). SPEC §4.5(관측 정의가 있는 절; 지시의 "§4.4"는 PLAN 절이라 관측 정의가 있는 §4.5를 고쳤다)·§6 INV-03 주석·REQ-01 override(`observation_notes`)를 함께 고침. 팩 revision 3(REQ-01 revision 3), 정책 예시 2개의 팩 revision을 3으로.
- 2026-10-19 · `routes_tested`의 정의를 "직통 시험 범위"로 바로잡음: 모든 (대상, 비허용 키) 조합의 직통 결과가 있는 경로. 이전에는 Gateway 허용 키 결과도 요구했는데, 상태 변경 경로는 Gateway로도 보내지 않으므로 추론 기록이 있어도 시험 범위에 들 수 없었다. Gateway 정상 대조 범위는 판정표의 `gateway_outcomes ⊇ routes_registered`(기능 축)가 따로 본다. 판정표 자체는 변경 없음.
- 2026-10-19 · 출발점이 여럿이고 일부만 연결되면(예: 외부는 막히고 호스트는 연결) 연결된 조합은 HTTP로 시험하므로 상태 변경 경로는 그 조합 결과가 없어 미시험으로 남는다(PASS 불가).

### 기능 축 (2026-10-19, 사람 결정)
- 2026-10-19 · 기능 축은 `/v1/completions` 정상 대조로 시작하되, 경로를 코드에 두지 않고 REQ-01 override의 `functional_probes`(method·path·body·expect) 목록으로 둔다. 판정은 "목록의 모든 항목이 기대대로 통과"(`functional_probe_results` all_in [pass], 계획 목록 ⊆ 결과, 빈 목록은 PASS 아님), 기대 코드가 아닌 HTTP 응답은 fail → FAIL, 응답 없음은 no_response → UNKNOWN. 목록이 늘어도 판정표는 그대로. 본문의 `"{model}"`은 ENV 모델 이름으로 치환.
- 2026-10-19 · 정상 대조(EVD.normal_control)도 같은 표본으로 정한다(전부 pass일 때만 succeeded). 실행기의 `/v1/completions` 하드코딩을 제거.
- 2026-10-19 · 보고서 행에 `functional_scope`("기능 확인 범위: N / 등록 경로 M (표본)")를 표시. 어느 관측을 셀지는 CheckDef `functional_scope`(probed_obs, of_obs)에 두고 코어는 이름을 모른다. 값이 없으면 직렬화에서 빠져 기존 RPT 해시 유지.
- 2026-10-19 · `gateway_outcomes`(등록 경로 전부의 허용 키 Gateway 응답)는 관측·증거로 계속 남기지만 판정표에서는 쓰지 않는다. 팩 revision 4, 정책 예시 팩 revision 4.

### 경로 분류·inventory 정의 (2026-10-19, 사람 승인)
- 2026-10-19 · `adapters/vllm.py` 경로 분류: `GET /load`, `GET /kv_event_sources` → INFO(REQ-01 대상에서 제외). `POST /v1/responses/{response_id}/cancel` → MUTATING(보내지 않음; TCP 연결 실패 시에는 추론 기록). 경로 변수(`{response_id}` 등)는 존재하지 않는 고정 ID `guard-probe-nonexistent-id`로 치환해 보낸다(관측·판정의 경로 키는 openapi 원문 템플릿 그대로).
- ~~2026-10-19 · 모델 inventory 정의: "snapshot 중 vLLM이 읽는 파일만"~~ → 같은 날 아래로 수정.
- 2026-10-19 · **모델 inventory 정의: 비교용 inventory = 마운트한 snapshot 전체, 참고용 = 엔진이 읽은 파일.** 이유: "엔진이 읽는 파일"은 엔진 버전마다 달라질 수 있어, 후보 버전이 다른 가중치 형식을 고르면 차이가 가려진다. (사람 결정)
  - 비교용 → ENV `model.inventory_sha256`, EVD `comparability.model_inventory_sha256`(INV-09): `tools/model_inventory.py`(기본 `--scope all`). opt-125m@27dcfa74: **12 files, 753,111,181 bytes, `d677f3999b13aab7bf9a7d203568ca63bc2d3fdc6a102b5efc2c719fd816ae6e`**.
  - 참고용 → ENV `model.files_read_by_engine`(판정·비교 조건에 쓰지 않음): `--scope vllm` = 설정·토크나이저 파일 + `load_format=auto`가 고르는 가중치 형식 하나(*.safetensors > *.bin > *.pt, index 포함). opt-125m: **7 files, 251,897,335 bytes, `b11bc2cafab45720afd3d2f19c8250352977bc216efb88fc0afb9d90edc2aea6`**. 비어 있으면 직렬화에서 빠져 기존 ENV 해시 유지.

### REQ-03 (2026-10-19, 사람 결정·진행 중)
- 2026-10-19 · **검증된 안전 프로브**: 상태 변경 경로는 어댑터(`adapters/vllm.py` `SAFE_PROBES`)의 "엔진 버전 + 메서드·경로 + 안전 요청 + 기대 응답(expected_handler_reached) + 근거 소스 위치" 표에 있는 것만 보낸다. 코어에는 없음. 표에 없는 버전·경로는 계속 보내지 않음(미시험 → UNKNOWN).
  - 확인 방법: 대상 이미지 `vllm/vllm-openai-cpu@sha256:495c84fe…`를 `--network none` 일회용 컨테이너로 띄워 `vllm/entrypoints/serve/elastic_ep/api_router.py`(sha256 `2d6805874a86…`)를 직접 읽음. `scale_elastic_ep`(L37-49): 본문 `{}` → `new_data_parallel_size` None → `HTTPException(400, "new_data_parallel_size is required")`, `client.scale_elastic_ep`(L64) 미도달. 선행 `Depends(validate_json_request)`(`serve/utils/api_utils.py` sha256 `ffb5e08c…` L350-356)는 Content-Type만 검사. `is_scaling_elastic_ep`(L83-85): 전역 플래그 읽기만. 사용자 사본의 설명과 일치.
  - 표: `0.31.0 POST /scale_elastic_ep {}` (Content-Type application/json) → 핸들러 도달 시 400 + "new_data_parallel_size is required"; `0.31.0 POST /is_scaling_elastic_ep {}` → 200 + "is_scaling_elastic_ep".
  - 판정: 응답이 표의 기대 응답과 일치하면 실행기가 `handler_reached`(인가 없이 핸들러 도달)로 분류한다. 일반 4xx는 handler_reached가 아니다(그 밖의 코드 → UNKNOWN). 대표값 순위는 2xx와 같은 최상위.
  - 안전 프로브를 보낸 실행에서는 전후로 신뢰 출발점 `/health`와 기능 표본 1회(추론)를 확인해 증거(`safety.before/after`)에 남긴다.
- 2026-10-19 · REQ-03 판정표: PASS = 관리·진단 등록 경로 nonempty·전부 시험·`nonadmin_outcomes`(키 없음·잘못된 키·일반 역할 대표) 전부 401/403. FAIL = 2xx 또는 handler_reached. 기능 축 = `functional_probes`(일반: `POST /v1/completions` 200, 관리: `GET /metrics` 200). 대상 경로 = 어댑터 종류 admin·diagnostic − 정책 `general_allowed_kinds`. 진단 = `/metrics`, `/load`, `/kv_event_sources`; 생존 확인(`/health`, `/ping`, `/version`)·문서(`/openapi.json`, `/docs`, `/redoc`) 제외. 예시 정책: 일반 호출자 = 추론만.
- 2026-10-19 · **REQ-01 판정표에도 같은 해석을 추가**: `fail_when_any`에 `direct_outcomes any_in [handler_reached]`. 근거: 위 결정의 "핸들러의 기대 응답이 오면 인가 없이 핸들러 도달 = FAIL"을 직통 경로에도 같게 적용. 현재 결과는 바뀌지 않음(exposed는 이미 2xx로 FAIL, isolated는 TCP 추론). **사람 확인: 유지.**
- 2026-10-19 · **handler_reached는 검증된 안전 프로브가 있는 경로에서만 발생한다. 일반 4xx를 FAIL로 취급하지 않는다.** 실행기는 요청이 어댑터 표의 안전 프로브이고 응답의 코드와 본문 문구가 표의 `expected_handler_reached`와 둘 다 일치할 때만 handler_reached로 분류한다. 그 밖의 400·404·422 등은 코드 그대로(판정표상 UNKNOWN). 단위 테스트 `tests/test_handler_reached.py`.
- 2026-10-19 · `/v1/responses/{response_id}/cancel`의 종류를 **inference**로 바로잡음(상태 변경 표시 유지, 보내지 않음). 근거: handler가 `vllm/entrypoints/openai/responses/api_router.py` L110-112의 OpenAI 호환 사용자 API. REQ-03(관리·진단) 범위에서 빠지고 REQ-01(inference·admin) 범위에는 그대로 남는다. (사람 결정 (a))
- 2026-10-19 · 일반화: 어댑터 경로 종류 `info` → `liveness`·`docs`·`diagnostic`; CheckDef `route_scope`(include_kinds, exclude_kinds_policy)로 점검별 대상 종류 지정(REQ-01 = inference·admin, 이전과 같은 15경로); 기능 표본 `as_role`; 실행기 설정 `roles`(역할→키 환경변수, 5b 설정 `allowed_key_env`도 호환); 공통 모듈 `executors/http_common.py`; Gateway 프로필 single·role_open·role_enforced; FakeExecutor 점검별 시나리오(`fake.scenarios`). 팩 revision 5.

### Step 0 — 저장소 위생 (2026-10-19, 외부 검토 반영)
- 2026-10-19 · `.gitignore`: `.venv/ __pycache__/ *.pyc .pytest_cache/ .pytest_tmp/ .DS_Store out/test-runs/`. `.guard/`(로컬 작업 저장소)와 `out/`의 나머지는 사람 결정 전까지 무시하지 않는다(PROGRESS "Step 0"의 분류안).
- 2026-10-19 · `.gitattributes`: 기본 `* text=auto eol=lf`. **해시로 보호되는 파일은 `-text`(줄바꿈 변환 금지)**: `packs/**`(pack.yaml의 files_sha256가 파일 바이트 해시), `reference/framework/data/*.json`(변환기 원본), `tools/overrides/*.yaml`(팩 생성 입력), `out/**/*.json|*.jsonl|*.txt`(증거·보고서·inventory), `**/.guard/artifacts/**`·`**/.guard/work/**`(내용 주소 저장 — 파일 이름이 바이트의 sha256). `*.sqlite`는 binary. 근거: Windows 클론에서 CRLF 변환으로 팩 파일 해시가 어긋나 `load_pack`이 실패하고 테스트 수집이 깨진 외부 검토 사례. `docs/automation_matrix.csv`가 CRLF(파이썬 csv 기본값)라 LF로 바꿈(내용 동일).
- 2026-10-19 · 미사용 `httpx` 의존성 제거(코드에서 import하지 않음, `test_inv_14`의 금지 목록 문자열에만 등장). `uv lock --offline`으로 갱신 — httpx·httpcore·idna 제거. SPEC §10 주석도 고침. (CLAUDE.md의 허용 의존성 목록은 사람 문서라 그대로 둠)
- 2026-10-19 · 엔진 테스트 산출물은 실행별 새 폴더 `out/test-runs/<UTC 시각>-<pid>/<이름>/`에만 쓴다(이미 있으면 실패, 삭제·덮어쓰기 없음). `out/5b`는 보존 증거로 테스트가 읽지도 쓰지도 않는다(`PROTECTED` 단언).
- 2026-10-19 · **지원 플랫폼: macOS·Linux.** Windows는 지원하지 않는다. 외부 검토에서 보고된 Windows 실패 5건 중 원인이 분명한 4건은 `skipif(win32)`와 사유를 달았다: 닫힌 루프백 포트가 연결 거부 대신 timeout으로 보고됨 ×3(`test_inv_03`·`test_http_probe` 2건), 심볼릭 링크 생성 권한 ×1(`test_model_inventory`). 경로 표기 1건은 어느 테스트인지 확인하지 못해 알려진 제한으로만 적는다. 이번에는 고치지 않는다.

### Step 1 — 정확성 보강 (2026-10-19, 외부 검토 반영)
- 2026-10-19 · **등록·시험·관측 키를 `"METHOD path"`로 통일**(`http_common.route_key`). 이전에는 경로만 키로 써서(`routes.setdefault(r.path, r)`) 같은 URL에 GET·POST가 있으면 앞 메서드(GET)만 시험하고 PASS가 날 수 있었다 — 가상 세계 재현(`tests/test_method_keys.py`: GET /multi 401, POST /multi 200 → 수정 전 PASS)을 먼저 쓰고 실패를 확인한 뒤 고쳤다. TCP 연결 단계 추론의 근거(`direct_basis`)도 메서드별. 판정표·core·judges 변경 없음(키는 불투명 문자열). 어댑터 요청의 메서드가 등록 메서드와 다르면 실행기가 거부. 과거 증거(`out/5b`의 경로 키)는 그대로 재판정 가능.
- 2026-10-19 · **정책 값 타입 검증**(`core/select.py`): 지원 타입은 지금 팩이 쓰는 것만 `str`, `int`, `bool`, `list[str]`. `select`는 정책에 값이 있으면 타입을 확인하고, null·잘못된 타입·지원하지 않는 타입은 `PolicyValueError`(점검 ID·키·기대 타입 포함)로 **실행 전에** 거부한다. 값이 없으면 기존대로 `NO_POLICY_VALUE` 공백. 범용 정책 언어는 만들지 않는다.
- 2026-10-19 · **rule_table `value_policy`·`subset_of`·`not_subset_of`**(`judges/rule_table.py` 0.3.0): `value_policy: <키>`는 기대값을 정책 값(POL.values[점검])에서 읽는다. 키가 없으면 그 규칙은 판단 불가 → UNKNOWN(이유 "policy value missing"). `value`와 함께 쓰면 오류. `subset_of`(관측 ⊆ 기대, dict는 키 집합)와 FAIL 조건용 부정 `not_subset_of`. 판정기는 계속 순수 함수.
- 2026-10-19 · **REQ-01 허용 주체 ↔ 실행기 역할**: `direct_route`는 시험 시작 전(요청 0건) 설정의 역할 이름이 모두 정책 `allowed_callers`에 있는지 확인하고, 없거나 정책 값이 없거나 `none`·`wrong`(익명)이 허용 주체로 올라 있으면 거부(→ ABORTED → UNKNOWN). 허용 주체로 취급하는 역할 = 설정 `roles`(결과는 `direct_allowed_outcomes`, 판정 미사용), 비허용 = 키 없음·잘못된 키. 실험실 정책 `allowed_callers: [allowed]`(실행기 역할 이름). `examples/policy.yaml`의 `[gateway-sa]`는 FakeExecutor 시연용이라 그대로(가짜 실행기는 이 확인을 하지 않음).
- 2026-10-19 · 정책 값이 결과를 바꾸는 테스트: REQ-01 `allowed_callers`(일치 → 실행·FAIL, 불일치 → 요청 0건 ABORTED), REQ-03 `general_allowed_kinds`(같은 Gateway 구성에서 [inference] → FAIL, [inference, diagnostic] → PASS), `value_policy`(같은 증거에서 허용 목록에 따라 PASS/FAIL). PLT-01 허용 capability는 Step 3.
- 참고: 복잡한 판정이 필요한 점검은 rule_table을 키우지 않고 순수 함수 Judge로 따로 구현한다(외부 의견 수용, 이번에 구현하지 않음).

### Step 2 — 경로 분류 안전 확인 (2026-10-19)
- 2026-10-19 · `POST /v1/chat/completions/batch` **상태 변경 아님(소스 확인)**: `openai/chat_completion/api_router.py`(sha256 `77f858fe0079…`) L84-108 → `chat_completion/batch_serving.py`(sha256 `0daa49681783…`) L114-160, 대화 N개를 한 요청 안에서 동기 생성 후 반환. 파일 전체에 store·save·persist·write·queue·background 경로 없음. 계속 inference(비상태변경)로 보낸다.
- 2026-10-19 · `POST /v1/responses` **상태 변경으로 표시(보내지 않음)**: `responses/protocol.py`(sha256 `5fa24e326726…`) L210 `store` 기본 True, L167 `background`; `responses/serving.py`(sha256 `c701580373b3…`) L155-193 메모리 저장소(response_store·msg_store·event_store, 제거 없음)·background_tasks, L337-344 `VLLM_ENABLE_RESPONSES_API_STORE`가 꺼져 있으면 store 무시, L513-528 저장. 상태 변경 여부가 대상 서버 환경변수에 달려 있고 실행기는 그 설정을 알 수 없으므로 MUTATING(종류는 inference). 안전 프로브는 만들지 않음. 두 판단은 `adapters/vllm.py` `ROUTE_REVIEWS`에 근거 소스 위치와 함께 기록.
- 2026-10-19 · 결과: REQ-01 exposed FAIL(등록 15, 시험 13 — `/v1/responses`·cancel 미전송), isolated PASS(15 전부 TCP 추론 timeout). REQ-03 범위(관리·진단)에는 영향 없음.

### Step 3 — PLT-01 (2026-10-19, 사람 결정·진행 중)
- 2026-10-19 · **허용 목록이 판단 기준이다.** FAIL = 정책 허용 목록 밖 capability·마운트·장치가 **실제 실행 구성**(docker inspect, 컨테이너 안 모든 프로세스의 유효 capability)에 있음. 특권 모드·호스트 namespace 공유(pid·ipc·net·uts)는 허용 목록에 없으므로 있으면 FAIL. `require_non_root: true`면 UID 0 프로세스가 있을 때 FAIL. 호스트 자원(마운트 목적지) sentinel 쓰기 성공도 FAIL. 컨테이너 자체 파일시스템("/") 쓰기 결과는 증거에만(허용 목록 항목이 아님). tmpfs는 호스트 자원이 아니라 마운트 관측에서 제외(증거에만).
- 2026-10-19 · 유효 capability는 `/proc/1/status`만이 아니라 컨테이너 안 **모든 프로세스**(`/proc/[0-9]*/status`)의 CapEff 합집합. 실측: vllm(PID 1)·python·VLLM::EngineCore·VLLM::Worker·exec한 sh 5개 프로세스. exec는 읽기 전용 명령과 sentinel(쓰기 성공 시 즉시 삭제)뿐. `Config.Env`는 기록하지 않는다.
- 2026-10-19 · 실행기 `container_config`: 대상 컨테이너는 설정 `[container_config].container`(없으면 target.engine.container). 기능 표본은 `via: engine`(신뢰 출발점 → 엔진).
- 2026-10-19 · 일반화: CheckDef `functional_probes[].via`(gateway|engine), CheckDef `limitations`(선정 시 보고서 한계에 그대로), rule_table `when_policy`(정책 bool이 true일 때만 규칙 적용), Docker 래퍼 `cap_add/cap_drop/read_only/tmpfs/user/security_opt`·`exec`·`inspect_json`, 실행기 골격 `requires_roles`·`requires_route_scope`, 실험실 엔진 프로필 default·hardened·overprivileged(모델 마운트는 모든 프로필에서 `:ro`, 위반은 빈 sentinel 디렉토리 rw만). 팩 revision 6.
- 2026-10-19 · 실험실 정책(PLT-01): `allowed_capabilities: []`, `allowed_mounts: ["/models:ro"]`, `allowed_devices: []`, `require_non_root: true`, `allowed_exec_writable_paths: ["/guard-cache"]`.
- 2026-10-19 · **hardened = (c)**(사람 결정): `/tmp`는 tmpfs `noexec` 유지, 컴파일 캐시 전용 exec 가능 tmpfs `/guard-cache`. 캐시 경로 환경변수는 실제 기동 오류로 확인: 1차(`TORCHINDUCTOR_CACHE_DIR`만)에서 실패 경로가 계속 `/tmp/.cache/vllm/torch_compile_cache/…/inductor_cache/…main.so` → vLLM 자체 캐시 루트 `VLLM_CACHE_ROOT`(이미지 `vllm/envs.py` sha256 `1d7101800f67…` L706-709, `compilation/backends.py` L1064) 필요. 최종: `VLLM_CACHE_ROOT=/guard-cache/vllm`(실측 3.0M 사용), `TORCHINDUCTOR_CACHE_DIR=/guard-cache/torchinductor`(오류 메시지가 지목, 실측 사용 0 — /tmp로 가지 않게 유지). `TRITON_CACHE_DIR`는 근거 없어(CPU, 디렉토리 생성 안 됨) 넣지 않음. (a) /tmp exec, (b) 컴파일 끄기는 쓰지 않음.
- 2026-10-19 · **쓰기 가능 + 실행 가능 마운트는 그 자체가 권한**: 정책 `allowed_exec_writable_paths(list[str])`, 관측 `exec_writable_mounts` = 컨테이너 `/proc/1/mounts`에서 rw이고 noexec가 아닌 실제 파일시스템(proc·sysfs·cgroup·devpts·mqueue 등 제외) 중 컨테이너 기본 사용자로 쓰기 시도가 성공한 마운트 지점. 파일 마운트(`/etc/hosts` 등)는 디렉토리가 아니라 제외. `subset_of`/`not_subset_of` + `value_policy`로 판정. hardened는 이 목록에 `/guard-cache`가 있어서만 PASS(목록을 비우면 FAIL — 단위 테스트), `/tmp`에 exec를 준 `tmp_exec`는 FAIL.
- 2026-10-19 · 보고서 한계 문장에 정책 값 채우기: CheckDef `limitations`의 `{키}`·`{len:키}`를 보고서가 정책 값으로 채운다(`core/report.py` `_fill_policy`). PLT-01: "실행 가능한 쓰기 경로 1개가 정책으로 허용됨 ["/guard-cache"] — 엔진 컴파일 캐시…". 팩 revision 7.
- 2026-10-19 · out/5b: 덮어써진 작업 트리 실행본(exposed·isolated·inv15, 팩 revision 5 기준)을 `out/archive/5b-after-req03/`로 복사(31파일, 해시 확인, `.gitignore`). 고정 증거는 커밋본(팩 revision 4) — 복원은 사람이 git으로. `.guard/`·`out/` 분류안 승인 → `.gitignore` 반영. Windows 경로 표기 1건은 알려진 제한으로 유지.

### Step 4 — 팩 보존본 (2026-10-19, 사람 승인)
- 2026-10-19 · `guard select`가 읽은 팩을 저장소의 내용 주소 파일로 보존한다(`store/packs.py`): 매니페스트 정렬 JSON 바이트 → `artifacts/<팩 Ref sha256>`(Ref의 sha256이 곧 그 바이트의 해시), 매니페스트의 파일 → `artifacts/<파일 sha256>`. 새 객체 종류·DB 스키마 없음, `guard verify` 그대로 통과.
- 2026-10-19 · `guard judge`·`guard report`는 디스크 팩이 PLAN의 팩 Ref와 다르면 보존본을 읽는다(매니페스트·파일 해시와 id·revision 모두 검증). 보존본도 없으면 지금처럼 중단. **`guard run`은 계속 현재 팩만**(과거 정의로 새 시험을 돌리지 않음). 보존본을 쓴 보고서는 한계에 "팩 보존본 사용(revision N)".
- 2026-10-19 · `guard pack import --pack <폴더> --store <저장소>`: 과거 팩 폴더(예: `git archive <커밋> packs/PACK-BASELINE`)를 해시 검증 후 보존본으로 넣는다.
- 2026-10-19 · models: 폴더·보존본 공용 검증 `build_pack(매니페스트, 파일 바이트)`, `Pack.path` 선택·`Pack.source`(dir|store).
- 2026-10-19 · **커밋본 out/5b(팩 revision 4) 재판정 수동 확인**: `git archive HEAD out/5b/…`와 `git archive 6d9ad83 packs/PACK-BASELINE`(매니페스트 sha256 `0eeebd125e50…` = PLAN의 팩 Ref)를 `out/test-runs/rejudge-5b-20261019T111117Z`에 꺼내 복사본 저장소에 import → judge → report. 원본 `out/5b` 해시 불변. 결과: exposed FAIL/PASS, isolated PASS/PASS, inv15 UNKNOWN/UNKNOWN — **기존 보고서와 판정·검증 수준·기능 확인 범위 동일**. 차이는 (1) 한계 "팩 보존본 사용(revision 4)" 추가 (2) RES 새 revision(@2): 판정 플러그인 버전 0.2.0 → 0.3.0(판정표 params_sha256·판정·이유·누락 관측 동일). 팩 revision 5(작업 트리·archive·`out/5b/req03-*`)는 커밋된 적이 없어 재판정 불가(보고서·증거는 읽을 수 있음).

### A — 수동 증거 입력 (2026-10-19, 사람 승인)
- 2026-10-19 · **사람의 판정 = 신고된 관측, 도구의 판정기가 PASS 관문을 적용해 RES를 만든다.** SPEC §4.5·§7에 명시. 추가 조건: (a) 신고된 PASS는 첨부 증거 1개 이상, 없으면 UNKNOWN (b) 보고서 한계 "수동 입력자는 인증되지 않은 자기 신고". 필수 관측 문장(CheckDef.required_observations_prose)마다 설명이 없어도 PASS → UNKNOWN(양식이 그 칸을 주므로 같은 원칙으로 적용).
- 2026-10-19 · DECLARED 수동 증거가 있어도 official 인정. 보고서 상단에 영역별 "수동 입력 N건 / 자동 측정 M건"(`RPT.evidence_counts`, 미실행 건수도 JSON에).
- 2026-10-19 · 자동 실행기가 있는 점검은 `override_reason`일 때만 수동 증거를 받는다. 대체 규칙(SPEC §4.5): 최신 revision이 판정에 쓰이고, 출처가 다른 이전 COMPLETED revision이 있으면 보고서 행에 "대체됨". **`guard run`이 사람이 입력한 최신 증거 위에 새 revision(NOT_RUN 포함)을 말 없이 만들던 경로를 막음** — 기본은 수동 증거 유지, `--replace-manual`(RunConfig.replace_manual)일 때만 대체하고 그때도 "대체됨" 표시.
- 2026-10-19 · 판정기 선택은 실행기 레지스트리 메타데이터 `EXECUTOR_DEFAULT_JUDGES`(`manual-entry` → `declared`)를 증거 생성 시 `execution.executor.default_judge`로 기록하고, 코어 judge는 그 값만 읽는다(문자열 비교·executors import 없음, INV-14).
- 2026-10-19 · 명령: `guard evidence template --plan …`(선정된 점검 중 자동 실행기가 없는 것의 빈 YAML 양식: 정상 대조 설명·챌린지 단계 문장·필수 관측 설명·충족/미충족/미확인 기준, `--include-automated`), `guard evidence import 파일.yaml`(첨부 경로는 파일 기준, 전부 검증 후 쓰기). `evidence add`는 만들지 않음(사람 승인 범위). 양식 필드는 `result_template.json` 참고(3축 판정, normal_control_result, evidence, limitations, started_at/finished_at).
- 2026-10-19 · 모델: `Execution.operator{by,at}`·`override_reason`, `ExecutorInfo.default_judge`, `CheckRow.evidence_source`·`replaced`, `Report.evidence_counts` — 모두 비어 있으면 직렬화에서 빠짐(기존 해시 유지). 사람이 입력한 증거(operator 있음)는 모델이 MEASURED를 거부.

## 열린 질문

### RB2 실행 준비물 결합값(B2) 분리 (2026-11-14, 미승인)
B2 57개 키(17개 점검)를 실행기 코드로 확인해 G(guard 계산 20)·E(기업 결정 포함 4)·P(실행 준비·실행 중 값 33)로 나눴다. 분류표·설계·영향은 [docs/RUN_BINDING.md](docs/RUN_BINDING.md) 7절. 승인 전에는 구현·팩 판본 상향을 하지 않는다.
- **RB2-Q1** G 20개(`transition_inputs_sha256`, `transition_*_generation`, `init_scenario_sha256`·`recovery_scenario_sha256`, `subject_scope`·`api_hop_scope`)를 결합 계산으로 옮기나? 시나리오는 실행기 설정 `scenario` 파일을 새로 받아 해시하고, 세대는 설정 `phase_generation`에서 읽는다. 잠정: 옮긴다.
- **RB2-Q2** 기록 세션 9개(`retry_session`·`observation_session`·`identity_record_session`·`record_inventory_session`·`collection_session`·`copy_record_session`·`route_session`·`forwarded_session`·`stored_session`)를 실행기 설정 `record_session`으로 옮기나? lifecycle prepared의 `session`은 다른 생산자 폴더 열쇠라 쓰지 않는다. 잠정: 옮긴다(기록 생산자 준비물 매니페스트는 후속).
- **RB2-Q3** LIF-05·06·16 표본 시각 12개를 설정 `sample_offsets_ms`(실행 시작 기준)로 바꾸나, 절대 시각을 설정에 두나? 잠정: offset. 실행기의 순서·240초 검사는 그대로.
- **RB2-Q4** E 4개(`retry_scope_sha256`·`copy_inventory_sha256`·`record_subject_plan_sha256`·`recovery_selection_sha256`)는 파일 안 기업 결정(재시도 한도, 복사본 조치·마감·예외, 접근 허용 주체·정책 참조, 행별 `require_absent`)을 먼저 분리해야 한다. 이번에는 정책에 남기고 분리안(7.6)을 점검별로 따로 승인받나? 잠정: 남긴다.
- **RB2-Q5** REQ-02 `route_file_sha256`은 실행 중 생산자가 쓰는 파일 bytes 해시라 실행 전 값이 없다. 옮기면 "선언 파일=읽은 파일" 대조가 빠지고 읽은 해시를 기록만 한다(수락 기준 축소). 잠정: 정책에 유지(REQ-02는 1차 UNKNOWN 범위).
- **RB2-Q6** PLT-07·08 실행 신원 11개를 실행 전 결합 파일(`egress_binding`·`api_binding`)로 옮기나? 실행기는 실행 중 Docker로 찾은 값과 계속 대조한다. 잠정: 옮긴다(1차 UNKNOWN 범위라 순서는 뒤).
- **RB2-Q7** 호환: 팩 @36, `keys_for` 단계별 판별(@35 재생에서 B1 유지), B2 값이 있는 결합 문서만 `guard-run-binding/2`(`verify`는 /1·/2 수용), 정책 이전 도구 `--stage 2`가 빠진 값을 설정 후보로 출력. 잠정: 이 방식.

### RB 실행 결합값 분리 (2026-11-14) — 사용자 승인·구현됨
- ~~RB-Q1~Q5~~ → 2026-11-14 사용자 "승인, 구현 시작"으로 다섯 질문의 잠정안을 승인했다. 구현과 설계안과 다른 점은 [docs/RUN_BINDING.md](docs/RUN_BINDING.md) 6절에 있다. 결정 요약은 아래 "2026-11-14 — RB" 절.
