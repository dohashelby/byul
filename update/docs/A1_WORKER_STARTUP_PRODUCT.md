# Worker startup·OBS02 공통 계측

상태: 구현 중인 native19 초안. runtime code literal은 아직 준비되지 않았고 소스 고정·시험·설치·실제 실행을 하지 않았다. 준비된 제품 지원이나 기존 native18 실제 검증으로 집계하지 않는다.

기존 native8/16/17/18 소스와 B readiness callback/helper bytes를 보존한 private 조합이다. 새 main vllm-native-lifecycle-metadata/19, recording guard-native-lifecycle-events/19, Worker event/link/close/limit 각각 /1을 명시한다. 기존 OBS02 observation_identity 제품 경로와 설정·EVD/RES/RPT/verify/export/restore/정확 Ref 소비를 연결한다. B tools.b_lif07_readiness의 NATIVE/Unready HTTP/delay 계약은 아직 None이며 B 전용 소비·판정·pin 갱신은 B가 맡는다. LIF09의 native14 MPI/READY 대기 분기는 유지하고 이 새 profile로 대체하지 않는다. LIF09의 추가 결합은 후속 범위다.

고정 upstream db9527a46873454610df6dbedf79a36d6bf1a7f6, 기존 0.31 image a4a4c0437bf7240089da5f08aa370c4aee17ae5290f7a3b468825ee26c4c3a6b의 Worker.init_device/load_model/initialize_from_config/compile_or_warm_up_model 경계를 관측한다. 세 메서드는 tracing.instrument decorator가 있고 load_model만 직접 함수다. 기존 image layer cache와 공식 Git blob의 tracing 세 파일 bytes를 대조했다. OTEL unavailable의 원 body 또는 available의 정확 sync wrapper·closure func·고정 span label·backend alias·source/body/entry code를 구분하며 원 바깥 callable을 한 번 호출한다. decorator를 벗겨 호출하거나 trace 설정·전송·환경값을 수집하지 않는다. 바깥 CALL/RETURN은 내부 body 진입, 실제 worker 역할, 모델 준비·CUDA 완료·READY 전송·서비스 접수를 증명하지 않는다.

B 두 파일은 원 bytes 그대로 두 private module에 적재한다. 원 callback의 고정 from-import 한 간선만 해당 private helper로 연결하고 guard namespace·서비스 import hook은 바꾸지 않는다. callback/helper source SHA와 실제 함수 code를 확인한다. static expected code와 callback_code_verified·callback_error를 구분하고, 수집·첨부 오류가 앞선 모델 root 관측을 지우지 않게 한다.

startup context의 원 모델 적재 root FACTORY 기록을 journal lock 안에서 정확 seq·payload SHA·call/model opaque token으로 연결한다. 시간 중첩·동일 PID만으로 다른 thread의 모델을 귀속하지 않는다. root FACTORY는 iterator 진행·read·tensor 적용이 아니다. 기존 native18 file/mapping 후보는 같은 session/PID-start/source/model root에서 별개로 유지하며 모든 기존 tensor·worker·승인·loaded-memory·mapping-release truth를 False로 둔다. native19를 old profile로 relabel하지 않는다.

준비는 기존 prepare-model-read-scope 및 prepare-lifecycle-sensor --profile vllm-native-lifecycle-metadata/19 --model-read-scope SCOPE --output FRESH를 사용한다. 정확 ENV·target/run·model revision/inventory와 명시 nonsecret 범위를 묶으며 준비 시 모델 파일을 열거나 엔진을 시작하지 않는다. main/base8/iteration16/scope17/mapping18/contract/B callback/B helper/sitecustomize/settings/모델 scope, 총11자산이다. 기존 source/image/lab·정확 target 세대·native0.32 미지원 gate와 2MiB/2048행 한도를 유지한다. Worker 관측 256호출 상한은 명시 limit 기록을 남긴다.

선정 session의 writer가 seal/bound/종료로 닫힐 때 해당 observer의 B 약한 참조·pending만 정리하고 close 결과를 END 앞에 기록한다. busy/후속 실패·이미 닫힌 writer의 경합으로 확인되지 않은 정리는 gap이다. worker·Pod·모델·GPU를 정리하거나 다른 session을 닫지 않는다. 주체·허용 모델/파일/image/action·정책 판본/효력/철회·실제 배포/운영 범위는 기존 OBS02 U1~U6 미확정이며 UNKNOWN을 유지한다.

검증 계획: 정확 source/decorator·원 반환/예외·callback 코드 교체·세대/누락/중복/부분·scope 교체·상한·후속 수집/첨부·선정 observer 정리를 CPU EXAMPLE로 확인한다. CPU fixture의 함수 namespace·trace backend·모델·PID/start 재배치는 EXAMPLE이며 serving hook/GPU 실증으로 쓰지 않는다. 원 B Windows LIF07/LIF13 실패와 기존 CI 결제 차단을 보존한다. 원GPU창 종료·추가 Start0·46.66USD/과거사용량·보존120GiB EBS를 유지한다. C·Torch 검증의 새 11의존성 설치는 별도 답변 대기이며 이번 계측 개발은 기존 의존성만 사용한다.

