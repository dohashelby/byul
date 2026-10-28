# 다음 직렬화 묶음의 중복 대조

2026-10-28 main `4af4d31`, A PR48 `7acd3d0`·PR51 `0b82eea`·PR52 `49b9ac4`,
B PR46 `dad2795`·PR49 `4f58c14`·PR50 `636a2a2` 및 공유 선점 기록을 읽고 대조했다.

| 기존에 있는 것 | 입력 → 실행 → 관측/판정 범위 |
| --- | --- |
| main REQ-16 M12 | required_cache_contexts와 DECLARED 자료 → 기존 manual_cache_context/수동 import → 정책·증거·EVD/RES/RPT 대조. 실제 cache 소비는 아님 |
| 공식 SALT/hash/LoRA 시험 | salt·LoRA metadata·token prefix → cache hash/manager → 동일/다른 키·hit/miss. 인증 집단·실제 adapter bytes·GPU 소비는 별도 |
| A native/target/credential 진행본 | 요청·free·pool 및 ENV/엔진 소유 신원 → native 관측/수집 → 요청/세대 대응·자격 관측. 전송 중 payload buffer 재사용 시험은 없음 |
| B-1 PR46/49 | 요청 재사용 입력·template/MM override·plugin group → renderer/admission 또는 plugin loader → 수용/거부·정규화·import 순서. 전송 frame 수명은 별도 |
| B-2 PR50 | 직접 공급한 untyped msgpack bytes → 실제 decoder → 확장 거부·메모리 callback·오류 후 정상 처리. 전송 중 header/tensor frame 결합은 아직 확인하지 않음 |

REQ-16의 키/수동 판정은 새로 만들지 않는다. 고정 태그의 원래 `test_hash_block_tokens`를
기존 source-slice adapter로 실행해 2 passed/skip0을 확인했고 상세는 외부
`cache-official-reuse-1/`에 있다. 실제 객체 변경/consumer 공백을 단순 metadata 비교로 닫지 않는다.
REQ-15의 신원과 REQ-17의 timing도 이 hash 시험 결과로 대체하지 않는다. SUP-15의 생성물/warmup
호환성도 KV 소비 판정과 다르다.

이번 부족한 구체 분기는 REQ-14의 **전송 중 msgpack payload buffer 수명과 request/output frame 대응**이다.
고정 공식 PAYLOAD 시험이 그 assertion을 이미 갖고 있으므로 재작성하지 않는다.
`tests/v1/test_serial_utils.py` bytes SHA256은 매핑의 5d35ce3 판본과 이번 db9527a 태그에서
동일한 `c2e2fc3650603fde8e7ccae58218b2af2c39c316673ca622764446b96badfedf`다.
시험 파일 동일성을 엔진 전체 판본 동일성으로 확대하지 않는다.

추가할 것은 실제 codec/CPU Tensor·로컬 inproc 전송에 이 원래 시험을 연결하는 최소 dependency
adapter, 고정 source/의존성 검증 및 원문 없는 실행 요약이다. 같은 직렬화 기능의 PR50에 모은다.
새 실행기·native 센서·판정기·정책·제품 의존성은 만들지 않는다.
실제 engine constructor/model/HTTP/GPU/NCCL/PD 배포·peer 권한은 실행하지 않는다.
공식 fixture의 메시지 수·크기·threshold를 기업 예산이나 허용 정책으로 채택하지 않는다.
