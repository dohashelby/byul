# OBS02 native18 전용 context 후보의 제품 연결

A-1은 A-2 PR162 확정 0adedb5dd57a988450e0a69c0dacd4947ffdefb1의 전용 세 파일을 원본 bytes로 소비한다. 공개 분석은 `guard.collectors.native_model_mappings.analyze(packet, prepared)` 0.1.0, schema는 `guard-native-scoped-mapping-candidates/1`이다. 기존 `observation_identity:kubernetes` 0.3.0, OBS-02 선택·실행·판정·보고·증거 경로에만 붙인다. native18 prepared와 닫힌 기존 정책·소스·이미지·lab gate를 유지하며 native17/19와 B 소유 구현을 대신 변경하지 않는다.

summary의 `projection.scoped_mapping_contexts`는 원 전용 분석이다. EVD `identity_native_models` 안의 `configured_model_candidates[].scoped_mapping_context_candidates`는 동일 recording/PID/start/model object와 이미 관측된 weight iteration의 loader call/root call/factory/마지막 seq가 일치하고 forward 이전인 후보만 포함한다. 잘린 source_events만 비교하지 않고 first/last locator와 root 마지막 seq를 함께 비교한다. 같은 경로·해시·모델 object나 시간 근접만으로 다른 세대를 합치지 않는다.

전용 소비와 첨부는 공통 mapping 관측 뒤 별도 경계에서 수행한다. 첨부 목록을 모두 계산한 후 반영하며 소비 실패는 `NATIVE_MODEL_MAPPING_CONSUMER_UNAVAILABLE`와 ABORTED를 남긴다. 앞선 공통 weight/mapping 사실과 독립 FAIL을 보존한다. 공통 소비 자체가 실패했다면 전용 함수를 다시 호출해 관측을 되살리지 않는다. 원문 예외·파일·주소·env/argv·요청/응답은 저장하지 않는다.

선정 검증은 전용 분석의 정상·오류·누락·교차 세대·출력 한도와 A-1의 전후 root/마지막 locator·원자적 첨부, 기존 OBS02 CLI의 정상·부분·후속 기록/첨부·전용 소비 오류 및 saved pack 정확 Ref 왕복이다. 기존 교체/restart·정책/소스/image/lab/version 거부와 독립 FAIL 보존은 같은 계약의 인접 회귀로 확인한다. 고정 소스와 결과는 A-1 인계에 별도 기록한다. 이 문서는 검증 결과를 미리 통과로 선언하지 않는다.

EXAMPLE safe_open 대역과 실제 C CPU 구성요소 근거는 구분한다. PR164의 원래 실패를 보존한 5개 원 통과 + alias namespace 보수 1개 통과를 재사용하며 실제 C packet을 새 제품 수집으로 소급하지 않는다. 실제 worker/serving/backend/GPU, 승인·검사 생산자/정책, 전체 inventory·최종 tensor 적용·mapping 연속성/해제와 SUP05/06 전체 분기는 미완료다. 모든 관련 truth flag는 false, security는 UNKNOWN이다.

공통 소스가 바뀌므로 A측 전체 고정 소비 기준을 검토 후 새 커밋에 고정한다. B160의 기존 A152/A156/A158 승인 쌍·원본 c9 pin·helper·전용 파일은 보존한다. 새 A 공통 기준은 B가 검토·고정하기 전 거부되는 것이 정상이며 실행 중 읽은 hash를 승인하거나 검사 경로를 제외하지 않는다. CI5는 정확 HEAD 성공 전 미확인이고 결제 차단을 코드 실패와 구분한다. AWS/GPU/추가 Start·비용·권한 변경은 없다.


## 고정 검증과 한계

통합 구현/공통 pin source a8b8f802cd7616614b289eb5087a9681315f734e에서 Windows/Linux 각각 78통과·4실패·9준비오류를 보존했다. 새 A1 시험의 기존 case/external fixture import 누락과 B collect_http 호출 flag의 의미를 보수한 4117158671311355d5e2b50debceaf070336cb7c에서는 각각 26통과·3실패였다. native18 전용 제품 시험 25개는 이 소스에서 모두 통과했다. 나머지 실패는 B 거부 코드가 요약이 아닌 저장된 http_observation.gaps에 있는 것을 시험이 잘못 찾은 경우다. 보수 source 60771416b05bef7887d324d82ced19d966c92181의 B 음성 시험 4개가 양쪽 모두 통과했다. runtime·A2 원본 bytes·gate는 처음 고정한 구현과 같다. 이 세 실행을 단일 최신 전체 suite 통과로 합산하지 않는다.

6077141 인접 회귀는 Windows 258통과·기존 INV03 Windows skip1·기존 engine 선택제외1, Linux 259통과·선택제외1이다. 대상은 기존 mapping sensor, OBS01/OBS02 identity/state, native17/19와 weight iteration 제품, 불변식이다. lab/source/image/native0.32 거부·교체/restart·후속 오류·UNKNOWN/독립 FAIL 보존의 기존 계약을 재사용한다. 새 skip·검사 제외는 없다.

wheel SHA256 0e35951d1e545a6494790679dc5896e4d8cf4ebc781c680a7e771e2968e42413의 guard280파일이 Windows source/설치본과 일치했다. Windows 추가 설치 CLI의 최초 실행은 pytest 미설치로 사건 실행 전 종료됐고 기록을 보존했다. 새 wheel 전용 venv에서만 기존 승인 시험 의존성 경로를 추가해 4사례가 통과했다. 원 환경/프로젝트 의존성·버전은 변경하지 않았다. Linux도 새 격리 wheel과 기존 승인 의존성을 사용해 동일 4사례가 통과했다. 각 설치본은 정상·부분·첨부·전용 소비 오류와 saved-pack 정확24Ref를 확인했다.

소스 native18 CLI9사례 + B 미승인 source 거부 CLI4사례는 각 OS 13개의 원 반출물·정확78Ref를 보존했다. 설치본 각4사례·정확24Ref는 별도다. 원 exporter의 evidence.zip을 그대로 반출하며 재생성본으로 바꾸지 않는다. A1 인계의 public-products-and-installed-evidence.json과 Windows/Linux 원 제품 evidence zip에 사례·원 zip SHA·Ref·source/설치 범위가 있다.

검토 공통 commit 3e105b75b7a1c24bf0bab7e950667581063b43b4, 전체388파일 manifest SHA256 b4c1cbb2041526fb7ad8d75fad89a56765d4dc42324ebca81da6b5617890ffdf를 고정했다. B160 원 A152/A156/A158 세 승인 쌍은 보존돼 새 A 기준을 LIF07_NATIVE_APPROVED_SOURCE_GATE_UNAVAILABLE로 거부한다. B main도 이 거부를 원 관측에 저장하며 HTTP/target exec0·UNKNOWN·정확 Ref를 보존한다. 기존 A158의 B HTTP 양성 실증을 새 공통 source로 소급하지 않는다. B가 새 고정 source와 소비 pin을 검토할 때까지 이 경로는 미승인이다.

실제 serving/worker/권한 생산자·기업 정책·최종 tensor 소비·scope 전체/해제·새 GPU 실증과 부모/A1 전체 완료는 미완료다. PR164 실제 C CPU 구성요소 원 증거와 이번 EXAMPLE 제품 관측은 별도다. 정확 최종 HEAD CI는 인계에 기록하며 CI5는 원격 성공 전 미확인이다. 결제 설정·CI 재실행·AWS/GPU·Start·IAM·비용 상한 변경은 하지 않았다.
