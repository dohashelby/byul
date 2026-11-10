# B03·B04 입력 준비와 실제 실행 잔여

## B03 입력 생성·소비 검증

기준 source `4674762373feaca9097c6f4ad09a1ebb4907ed77`은 main `7a985052`를 포함한다. 현재 공개 복사본의 의존 파일은 이 고정본과 동일하다. 제품 목록은 528파일/manifest `6ba867aa1600ccd653c588387764c91196ec453904fa2e6ad2cffbab841b8e33`이다. 새 B 도구는 이 제품 목록과 별도로 집중 검토했다. 원 SUP16 runtime `2cb01e9f`/466파일과 구분한다.

[B 입력 생성 도구](../../../tools/b03_input_materialize.py)는 기존 `gpu_a_run_inputs.http_inputs`, `prepare_checklist_cases`, registry·정책 검증기를 사용한다. SUP-03·08, REQ-08·09·10·07, SUP-13의 normal/challenge JSON, EXAMPLE/DRAFT 정책, TOML 설정과 아직 결합되지 않은 target을 생성한다. OPT의 실제 고정 config에 맞는 vocab_size 50272를 연결했다. 원 Qwen 수치 151936을 그대로 사용하지 않는다.

| 확인 범위 | 결과 | 한계 |
| --- | --- | --- |
| 선정 7개 실제 생성 입력·정책 | 기존 LAB 정책 6개 수용, SUP-13 필수값 누락 진단 | 기업 정책 승인·현재 target 준비 아님 |
| 원 계약 producer→소비자 | 정상·도전 입력, wrong-model/hash/null-target 거부 등 직접 CPU 18경계/실패0 | carrier는 소비 필드용 SimpleNamespace. 실제 HTTP·worker 관측 아님 |
| 실제 CLI 설정 소비 | 추가 CPU 1경계/실패0, CLI 생성→TOML loader→RunConfig 구성→registry factory 생성 7개 | application.run·HTTP 실행0. 18+1을 제품 실험19개로 합산하지 않음 |
| 40개 정적 준비 | 정의 SHA·분류·선정 image/model/config·잔여 담당 연결 | 나머지 33개 역할별 target/prepared·정책 입력과 실제 관측은 잔여 |

CPython 3.12.3 `-I -S -B`, 실제 UID/GID1000/1000, FD4→4, source 불변 및 자식 종료를 확인했다. 직접 CPU의 runner elapsed는 2.008초, 추가 설정 소비는 1.769초다. 이는 기록된 UTC 시작/종료 차이와 별개이며 GPU 시간으로 환산하지 않는다. 기존18 범위는 설정 표현만 바꾼 v2에서 영향 없는 부분을 재사용했다. 원 첫 시도의 메타데이터·TOML 초안 오류와 stderr는 보존했다. 제품 guard·assert·기한을 완화하거나 skip을 추가하지 않았다.

S6 독립 검토는 250대조/불일치0, SHA `acee186a453d34b9e04976241d000cb6c97ca0704b9a087fc73941a26e61d9b9`다. 원 v2 source-freeze SHA는 `740a91d332c473c73b51b341efdc13f8bef677c044f8f2f7cd39eb6b3680e3bd`, 공개 도구 SHA는 `4ee13fb0633408f9462e4cf13440f7e125afbfd9021340d09c9747ff9711a873`다. 공개 복사는 원 bytes를 유지했고 private 실행 경로·계정·인스턴스 설정은 포함하지 않았다.

[정적 40행 요약](b03-filled-static-summary-20261110.json)의 CPU 검증 표시는 해당 EXAMPLE 입력 준비 범위다. `runtime_ready=false`와 current 신원 null을 유지한다. REQ-07·SUP-13은 실제 backend와 현재 SUP-03 전용 Kubernetes factory가 호환되지 않는다. SUP-13은 `forbidden_template_attributes`가 필요하다. 같은 engine의 공유 실행 가능 판정은 아직 없다.

## B04 구체화한 준비

main `7a985052`의 관련 21파일을 고정해 확인했다. native24의 mp-worker/RPC fault 제어·관측 코드는 존재하지만 rank0·TP/PP/DP1·world_size1의 지원 범위다. 이를 TP2 지원·공통 producer 신원 반환·GPU 등록 자격과 구분한다. B의 passive native13 경로와 B12 `producer=None`을 근거로 native24 코드가 없다고 표현하지 않는다.

| 점검 | 설계 case | 준비한 정상→장애→복구 연결 | 아직 필요한 실제 입력 |
| --- | --- | --- | --- |
| LIF-08 | exception, death, delay, communication-stall 4개 | request/attempt·nonce/session·PID/start·clock·감지/상위통지·release/정상 canary | TP2 제어/peer·공통 producer, 선택 rank/배정, 감지·통지 정책 기한 |
| LIF-11 | runtime-failure, partial-initialization 2개 | before/after 자원 census·실패 trigger·deadline basis·새 generation 정상응답·정리 | 실제 trigger/resource kinds·공통 group record·정책 기한·부모 상한 |
| SUP-06 | missing-selected-shard 1개 | 정상 required/assigned/load·별도 소유 사본 누락·새 복구 세대 | 실제 rank/shard 배정·정상/도전 target/prepared·실소비·정책/source 결합 |

설계 case7개×정상/장애/복구3단계×예정rank2개의 준비 디렉터리·요구행42개를 만들었다. 이는 실제 rank 배정·실험42회·실측 완료가 아니다. worker/PID/start·boot/UID·required/assigned shard·현재 정책·PACK34 Ref는 null이다. 기존 OPT의 단일 `model.safetensors`를 TP2 rank별 tensor shard 배정으로 간주하지 않는다.

기존 `b12_tp2_inputs.py --inputs ... --verify-inputs-root ... --output ...`를 실제 실행해 계획 제어 JSON9파일의 SHA·JSON object를 확인했다. exit0/stderr0/벽시계2.390초이며 결과는 **INCOMPLETE**다. local 미확정31개·전체 plan 미확정63개, runtime_binding/prepared_assets/ready_for_execution은 false다. 31/63은 결함수나 실험수가 아니다.

계획 슬롯 LIF-08 900초/LIF-11 600초/SUP-06 900초는 실제 부모 기한이 아니다. 정책 유효창, collector 기본120초, native/HTTP 상한300초, native24 hold최대30초·LAB RPC5초를 분리한다. 실제 부모·phase·정리 예약은 미입력이다. `response_ms`를 LIF-08 감지·통지 정책8키로 임의 변환하지 않는다. 기존 B12 단일 runtime_refs로 SUP-06의 두 역할·복구 세대 전체가 검증됐다고 표시하지 않는다.

S4 인계서 SHA `322d345dfc134d51a994ffde57b9e4a6977d53a506938ed306ae7a595361a48b`, 실제 사전점검 기록 SHA `16739ee83cc33385bcfb7f0158be074b5bda452755a12feecb525524fb04ece0`다. private 경로·원 신원 자료는 공개하지 않았다. 새 모델 다운로드·대용량 재해시·제품 코드 수정·AWS/GPU 실행은 0이다.

## 다음 반환과 실행 분리

S5는 B03 실입력과 CPU 준비, S4는 B04 case별 반환 요구, S2는 실제 운영·현재 신원·요청 대응, S6는 새 변경 독립 검토, root는 [실행 순서·공유 조건](B03_EXECUTION_SHARING_ORDER_20261110.md)과 PR 통합을 맡는다. A의 남은 공통 producer·정본팩·자격/TP2 반환과 권한자의 정책값은 해당 분기에만 적용한다. B03/B04를 현재 SUP16의 전역 시작 조건으로 붙이지 않는다.

PACK33 DRAFT·EXAMPLE·기존 FAIL/UNKNOWN/Refs·LAB10/33을 보존하며, 이 준비로 새 EVD/RES/RPT·PACK34 Ref나 실제 GPU 완료를 생성하지 않았다.
