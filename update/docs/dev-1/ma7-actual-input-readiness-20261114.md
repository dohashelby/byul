# M-A7·M-A8 실제 입력 준비와 남은 조건

2026-11-14 확인 결과다. REQ-17 구현/입력 v5는 [PR #267](https://github.com/dohashelby/byul/pull/267)에 있고 CI18개가 모두 통과했다. #265·#266은 main에 병합됐다. 이 후속은 실제 환경 자료를 만들지 않고 부족한 준비물과 필요한 접근 조건을 구분한다.

## 준비된 것

- SUP-08 실제 fixture: 모델14개8,295,393,861 bytes·코드 캐시5개·기동 파일12개. 2026-11-14 재검증34.018초. 경로는 작업 폴더 inputs/sup08-rebuild-20261113이며 v2 기동 자산을 유지한다.
- 입력 v5: prepared16개·서로 다른16세션·사례10파일/2078개·REQ17 그룹목록2개. source plan bytes와 모든 파일 SHA를 보존한다. source fc77399e에서 생성했으며 실제 ENV/target은 비어 있다.
- 추가 준비: REQ-15 기준/후보 prepared2개를 inputs/ma7-req15-supplement-20261114에 만들었다. [추가 기록](ma7-req15-prepared-20261114.json)에 파일/설정 SHA·세션을 남겼다. 관측 자산/생성기 bytes가 main과 같고 기존16개와 총18세션이 겹치지 않음을 확인했다. HMAC 비밀은 Git에 넣지 않았다. 노드 설치·실행 자격은미확인이다.
- 확정5값을 반영한 정책 초안은 존재하지만 실행별 ENV/target/prepared/manifest hash 결합과 운영 확인은 미완료다. DRAFT를 실제 승인으로 바꾸지 않는다.

## 실제로 비어 있는 것

69개/양쪽138쪽의 ENV와 target이 모두 미입력이다. M-A27개/양쪽54쪽에는 필요한 prepared18개가 존재하지만 실제 노드 설치·source/CPython 자격·정상 요청은 확인되지 않았다. REQ-15·19·20·21·LIF-13의 manifest 사례/대조/실험 소유 범위 입력이 남아 있다. 이 항목을 단순파일부재와 실제실행조건부재로구분한다.

- REQ-15: 실제 Gateway target·인증 주체/tenant 매핑·기업의 격리/공유 정책과 요청 사례가 필요하다. 예시 A/B 주체를 기업 값으로 사용하지 않는다.
- REQ-19·20: 소유 lab target과 실제 GPU 연산/독립 대조가 필요하다. REQ-20의 그래프 대조 출력은 실제 수집 전에는 지어내지 않는다.
- REQ-21: 소유 TP2 lab의 재기동 대상과 before/switch/after 관측이 필요하다. 종료·회수·새 세대 성공을 파일 존재로 대신하지 않는다.
- LIF-13: 실제 승인 가중치·daemon/소비자 소유 범위·세대/정책 결합이 필요하다. 기준 쪽의 승인된 기능 부재 UNKNOWN과 실제 생산자 자격을 구분한다.
- REQ-16: 후보 모델의 가중치·실제 두 판본 로드·LoRA/가중치 세대 입력이 남는다. 기존 메타데이터 확인을 가중치 다운로드/호환 실증으로 올리지 않는다.

## 실제 사전 점검에 필요한 자료와 접근

| 필요한 것 | 근거와 영향 |
|---|---|
| 접근 가능한 실제 GPU 클러스터/대상과 context·namespace·읽기/exec 권한 범위 | 기존 Windows kind/WSL minikube API는00:32 KST 조회에서 연결 거부였다. 새 접근 근거가 없어 반복 조회하지 않았다. 기존 AWS 회수 대상도 앞선 계정/서울 조회에서 없었고 다른 노드를 기동하지 않았다. 영향: A7/A8 실제 target·노드 배치/권한 |
| 기준/후보 실제 ENV와 Pod target 각2개 | 설치 엔진 판본·이미지 digest/imageID·모델 inventory·GPU/driver/TP, namespace/Pod UID·컨테이너ID/시작시각/재시작수 등을 실제 조회로 결합해야 한다. 근거: gpu_a_run_inputs.environment/target, engine_sensor._native, mvp_sup08_inputs.prepare. 영향: 실제 ENV/target138쪽·SUP08 최종4입력 |
| 노드의 모델/코드 캐시 배치·권한·정상 요청 | 로컬 파일 SHA는 노드 기동 성공이 아니다. 실제입력계약·입력사전점검·REGRESSED 정본실측은미완료다. 영향: SUP08·REQ16과 native 관측 실행 |
| 새 GPU/클라우드·추가 이미지/가중치가 필요한 경우의 구체 실행/비용 승인 | 기존 회수 승인과 Python 두 레이어 승인을 J01/J02 기동이나 다른 이미지/가중치로 넓히지 않는다. 실제 대상/요금 확인 뒤 별도 계획과 상한을 승인받아야 한다. 영향: 최종사전점검과GPU세션 |

현재 ready_for_execution/input_preflight_passed=false, 실제 결과null이다. 추가준비18개를 GPU 실측/등록이나전체M-A7완료로표시하지않는다. 실제 환경/승인이 없으므로 자료·접근/비용 조건만 구체화하고 독립적인 M-A3 CPU 개발을 계속한다. #263 draft·#264 미병합·M-B4 보관을 유지한다.
