# LIF-07 정상 전용 준비 절차와 CPU 검증

기존 `initialization:kubernetes`에 사용할 정상 준비 절차를 장애 주입·복구 절차에서 분리했다. 검증 기준 제품 소스는 `3bba3c73a6bee1966b784cd90a9690e3e9d2512a`다. 이 문서는 구현·CPU 검증·GPU 인계의 범위를 기록한다. 실제 GPU 실행 결과는 별도 실측 보고서에서 다룬다.

## 구현과 재사용 범위

정상 준비물은 render → 현재 gated 대상 결합 → release 계획 출력 → 같은 세대의 Ready 확인 → DRAFT 입력 생성 → 원 CLI 파이프라인으로 구성된다. 배포와 release 실행·정리·AWS 운영은 GPU 담당자가 맡는다. 실행기가 엔진을 생성하거나 재배포·재기동하지 않는다.

- 기존 native22 센서, ModelReadScope, UID1000 자산 생성·로드와 결합 검사를 재사용한다.
- 실제 Pod UID·container ID·시작 시각·PID1·노드·GPU·boot 신원을 전후 비교한다. 교체·재시작 후 옛 입력으로 진행하지 않는다.
- 실제 ready 생산자의 파일과 raw SHA를 입력 소비자에 결합한다. 실행 입력 생성 전 현재 reader snapshot과 Ready 결합을 확인한다.
- `lab_activation`·record attestation·주입 runner 없는 직접 `KubernetesInitializationExecutor`를 사용한다.
- 기존 CLI의 선정→실행→EVD→RES→RPT→반출·복원→저장 팩 재판정과 정확한 Ref 검사를 유지한다.

14개 실행·재사용 파일과 별도 manifest로 구성한 운영 패키지를 고정했다. manifest SHA-256은 `5b5e14dcae2c3005e50c9def993703a1f23304629d48760047506e6237d14132`다. 운영자가 새 디렉터리에 설치하고 전체 파일 SHA 및 UID1000·디렉터리700·파일600을 확인해야 한다. 패키지는 특정 실험 인프라의 고정 신원을 포함하므로 비공개 인계본으로 보존한다. 이 PR에 운영 패키지나 원자료·정책·설정 파일을 싣지 않는다. 공개 범용 준비 CLI를 추가했다고 주장하지 않는다.

## 기존 팩과 기한

기본 `PACK-BASELINE@32`는 이 native22 경로의 필수 관측 5개를 선언하지 않아 직접 실행할 수 없다. 기존 공식 importer를 사용하는 DRAFT 파생 `PACK-B-GPU-READINESS-GB49@1`을 그대로 재생성한다. Ref SHA-256은 `170f8d8935324ea322489ee793f05dcdcb3b9473e82100b925af222882170cc9`다. 원 질문·judge·activation 관측 4개·FAIL/UNKNOWN 기준은 보존한다. 기존 파생팩의 13개 준비 항목을 줄이지 않는다.

| 계약 | 유지한 값 |
| --- | --- |
| 정상 rollout / 외부 wrapper | 600초 / 620초 |
| 정상 수집 | 120초 |
| DRAFT 정책 유효창 | 570초 |
| DRAFT 준비·복구 한도 | 각각 120,000ms |
| 기존 장애 actor / 전체 | 60초 / 240초, 정상 전용 경로에서 호출하지 않음 |

정상 경로 분리는 기존 복원 지연 문제의 해결 증명이 아니다. 기존 native15 Pod에서 native22 시작 관측을 사후 생성하지 않는다. GPU 담당자는 새 gated 세대와 실제 ENV·target·ModelReadScope·세션을 준비한다.

## CPU 검증과 수정

| 검증 묶음 | 결과와 한계 |
| --- | --- |
| 자산·권한 계약 17군 | 실제 UID1000 native22 생성·로드·scope 결합 및 Root 전용 입력 접근 거부 확인. 모델은 명시적인 EXAMPLE 7파일 fixture |
| 입력·파이프라인 계약 13건 | 실제 ready 생산자→소비자, 기존 CLI 9단계, EXAMPLE EVD·UNKNOWN RES·비공식 RPT·복원 후 정확한 Ref 재판정 확인. 일부 I/O는 CPU 대역 |
| 대상·신원 결합 회귀 24건 | 최종 수정본에서 통과 |
| 필수 마운트 계약 23건 | 수정 전 22실패/1통과 원문 보존. 수정 후 23건 통과 |
| 마지막 패키지 권한·해시 3건 | 실제 UID1000 POSIX 환경에서 정상 허용·파일 변조 거부·잘못된 파일 모드 거부 확인 |
| 고정 검토 | 실제 renderer→projector의 메모리 사례 29건 확인, 잔여 차단 사항 0 |

검토에서 필수 writable mount의 `readOnly` 생략/기본값과 `subPath` 우회 검증 결함을 발견했다. 양쪽 spec에서 생략을 정확한 bool `false`로 정규화하고, 필수 mount의 경로 우회를 거부하도록 수정했다. 다른 13개 실행 파일과 원 센서·팩·judge·기한은 유지했다. 초기 fixture·권한 검증 실패와 이후 성공 로그를 모두 보존했다.

제품 소스·팩은 바뀌지 않아 해당 기준의 기존 전체 시험 11,060 통과와 무결성·설치 검증을 재사용했다. 새 운영 패키지의 직접 계약을 별도로 확인했고, 같은 제품 소스에서 전체 시험을 반복하지 않았다. 이 문서 추가는 제품 코드·테스트·빌드 설정을 바꾸지 않는다.

## GPU 인계와 남은 분기

비공개 인계본에는 고정 코드·manifest, 실제 parser로 확인한 단계별 argv 템플릿, 설치·실행·복구/정리 절차, 검토 receipt와 CPU 검증 기록을 둔다. 운영자는 패키지 무결성, 현재 소스·예산·비용 보호 타이머, 다른 작업 부재, 실제 신원과 새 세대를 확인한 뒤 실행한다. 인계 자체는 기동·시험 예약이 아니다.

이번 소스 작업의 AWS·SSM·GPU 실행 및 예약은 0이며, GPU 운영 환경·증거·비용 장부를 수정하지 않았다. 실제 모델 전체 bytes 검증, 운영 DMI·sudo 전체 진입 경로와 vLLM/GPU는 미실측이다. 정상 초기화 후보 수집과 독립 지연·미준비 요청 차단·모든 모델/worker 준비·service gate·producer 인증·복구 lineage는 구분한다. activation 누락의 UNKNOWN, DRAFT/EXAMPLE 한계와 원 질문 전체 미완료를 유지한다.
