# 체크리스트 GPU 의존성 1차 분류

> 작성: 2026-10-23. GPU 자원 계획을 위한 수동 설계 분류다. 실행·실측·정책 확정·자동화 완료를 뜻하지 않는다. CheckDef·판정 규칙·SPEC은 변경하지 않았다.

## 분류 기준과 해석

- **필수:** 적용 조건을 충족해 원문의 해당 시험을 실행하면 GPU kernel·CUDA Graph·native GPU IPC 등 실제 GPU가 반드시 필요한 항목이다. 기능이 미사용이면 적용 판단을 먼저 한다.
- **조건부:** CPU로 기본 분기·사전 시험을 할 수 있어도, GPU backend·TP·선택 기능·GPU 비용/성능·native 완료까지 시험 범위에 넣으면 GPU가 필요한 항목이다. 36개 모두가 매 실행마다 GPU를 요구한다는 뜻은 아니다.
- **불필요:** 점검의 핵심 보호 속성을 CPU 엔진·파일/네트워크 시험·외부 승인/자격/기록 시스템에서 검증 가능한 항목이다. GPU 없는 대체 환경의 결과를 고객사 GPU 배포 전체에 그대로 적용해도 된다는 뜻은 아니다.

**질문에 대한 자원 계획용 이진 집계:** GPU 할당 후보는 필수+조건부 **40개**, GPU에 독립적인 기본 시험은 **37개**다. 이는 최대 후보 행 수이며 GPU 장수·GPU 시간·고객별 실제 선정 시험 수가 아니다.

정상 대조가 GPU에서의 실제 추론·배포를 요구하도록 고객별 계획이 고정되면, 불필요 항목도 통합 검증 단계에서 GPU를 사용할 수 있다. 반대로 조건부 항목은 해당 기능의 비사용이 확인되거나 CPU 기본 분기만 선정되면 GPU를 배정하지 않을 수 있다. 따라서 최종 실행 계획에는 행 단위 분류 외에 선택한 분기와 정상 대조 자원도 명시해야 한다.

GPU 불필요는 다른 인프라·실제 기업 환경이 불필요하다는 뜻이 아니다. 기존 automation_matrix의 A/B/C 등급은 자동화 환경 분류이며 이 표와 서로 다른 축이다. 특히 B에는 GPU 외에 외부 저장소·collector·SDK 등도 포함되어 있으므로 B의 수를 GPU 필요 수로 읽지 않는다.

## 영역별 집계

| 영역 | 필수 | 조건부 | GPU 할당 후보 | 불필요 | 합계 |
|---|---:|---:|---:|---:|---:|
| SUP | 0 | 8 | 8 | 8 | 16 |
| REQ | 3 | 8 | 11 | 10 | 21 |
| PLT | 0 | 5 | 5 | 7 | 12 |
| LIF | 1 | 12 | 13 | 3 | 16 |
| OBS | 0 | 3 | 3 | 9 | 12 |
| **합계** | **4** | **36** | **40** | **37** | **77** |

## 항목별 근거

각 행의 질문·scope·applies_when·normal_control·challenge_steps·required_observations_prose·method_and_evidence를 읽고 분류했다. GPU라는 단어의 출현 여부로 자동 분류하지 않았다. 원문의 기본/선택 분기가 혼합된 경우 조건부로 남겼다.

| ID | 분류 | 점검 질문 | 분류 이유·GPU가 필요한 조건 |
|---|---|---|---|
| [SUP-01](../packs/PACK-BASELINE/checks/SUP-01.yaml) | 불필요 | 제출·저장소 쓰기 권한만으로 승인 기록을 바꿀 수 없는가? | 승인 저장소의 역할별 쓰기·승인 권한 시험이다. |
| [SUP-02](../packs/PACK-BASELINE/checks/SUP-02.yaml) | 불필요 | 배포 권한이 허용된 모델과 대상 환경에 한정되는가? | 모델×환경 배포 인가와 실제 선택 대상의 대조가 핵심이다. |
| [SUP-03](../packs/PACK-BASELINE/checks/SUP-03.yaml) | 조건부 | 실제 소비하는 하위 모델 자료가 승인 목록에 포함되는가? | config·tokenizer는 CPU로 확인 가능하나 GPU 모델이 선택하는 하위 모델·실제 소비 경로는 해당 backend에서 확인한다. |
| [SUP-04](../packs/PACK-BASELINE/checks/SUP-04.yaml) | 불필요 | 실제 실행 이미지·패키지·의존성이 승인 목록에 포함되는가? | 이미지 digest·entrypoint·패키지·마운트와 승인 manifest를 대조한다. |
| [SUP-05](../packs/PACK-BASELINE/checks/SUP-05.yaml) | 불필요 | 승인 뒤 파일·symlink·마운트가 교체되어도 비승인 사용을 막는가? | 파일·symlink·마운트 교체와 소비 전 무결성 통제는 CPU 파일 소비 경로에서 시험 가능하다. |
| [SUP-06](../packs/PACK-BASELINE/checks/SUP-06.yaml) | 조건부 | 각 worker가 자신에게 배정된 승인 모델 부분을 실제 적재하는가? | 단일 CPU worker 적재와 GPU TP rank별 shard·tensor 실제 적재를 구분해야 한다. |
| [SUP-07](../packs/PACK-BASELINE/checks/SUP-07.yaml) | 조건부 | 생성 결과나 공유 컴파일 캐시의 무단 교체를 막는가? | CPU cache 통제는 CPU로 시험 가능하나 CUDA·Triton 생성물의 실제 소비는 GPU 분기 시험이다. |
| [SUP-08](../packs/PACK-BASELINE/checks/SUP-08.yaml) | 조건부 | 요청 입력으로 허용 밖 코드 판본을 선택할 수 없는가? | processor의 코드 선택은 CPU 사전 시험이 가능하나 선정 멀티모달 모델·endpoint의 실제 소비 확인은 지원 backend에 따른다. |
| [SUP-09](../packs/PACK-BASELINE/checks/SUP-09.yaml) | 불필요 | 모델의 추가 코드는 허용 조건에서만 실행되는가? | auto_map·custom module의 선택·import·trust 통제는 무해 CPU fixture로 검증 가능하다. |
| [SUP-10](../packs/PACK-BASELINE/checks/SUP-10.yaml) | 조건부 | 플러그인 허용 제한이 실제 실행 전에 적용되는가? | entry point 발견·import 제한은 CPU로 확인 가능하나 GPU runner의 logits hook 등 실제 호출 분기는 별도 확인한다. |
| [SUP-11](../packs/PACK-BASELINE/checks/SUP-11.yaml) | 조건부 | 지원 실행 모드에서도 동적 import 제한이 유지되는가? | 선언한 지원 모드가 GPU 전용 runner·backend를 포함하면 그 모드의 import 집행도 시험해야 한다. |
| [SUP-12](../packs/PACK-BASELINE/checks/SUP-12.yaml) | 불필요 | 선정 reader가 허용하지 않은 객체·동작을 거부하는가? | reader·파일 형식·역직렬화 및 fallback 선택은 CPU 시험 자료로 검증 가능하다. |
| [SUP-13](../packs/PACK-BASELINE/checks/SUP-13.yaml) | 불필요 | 공급 template의 해석 동작이 허용 범위 안에 있는가? | template renderer·sandbox·출력·처리 예산 시험은 CPU 작업이다. |
| [SUP-14](../packs/PACK-BASELINE/checks/SUP-14.yaml) | 불필요 | 검사 실패·오류·미지원·생략을 통과로 처리하지 않는가? | 검사 원결과와 승인·배포 gate 상태 전이를 시험한다. |
| [SUP-15](../packs/PACK-BASELINE/checks/SUP-15.yaml) | 조건부 | 모델·도구·설정 변경 후 부적합한 파생 결과를 재사용하지 않는가? | GPU compiler·옵션 변경에 따른 cache 무효화·재생성은 해당 GPU 실행 경로에서 확인한다. |
| [SUP-16](../packs/PACK-BASELINE/checks/SUP-16.yaml) | 조건부 | 중단·실패한 생성 결과나 부분 파일을 완료된 결과로 사용하지 않는가? | 다운로드·부분 파일은 CPU로 시험 가능하나 GPU 컴파일 생성 중 중단·재소비는 해당 backend 시험이다. |
| [REQ-01](../packs/PACK-BASELINE/checks/REQ-01.yaml) | 불필요 | Gateway를 우회한 직통 요청으로 비허용 작업을 수행할 수 없는가? | Gateway·직통 경로의 인증·인가·네트워크 집행을 CPU 엔진에서도 시험 가능하다. |
| [REQ-02](../packs/PACK-BASELINE/checks/REQ-02.yaml) | 불필요 | 인증이 검사한 경로와 실제 실행 handler의 권한 요구가 일치하는가? | root_path·rewrite·인증 입력 경로와 handler 선택의 일치 시험이다. |
| [REQ-03](../packs/PACK-BASELINE/checks/REQ-03.yaml) | 불필요 | 일반 호출자가 비허용 관리·진단 작업을 수행할 수 없는가? | 역할별 관리·진단 route 인가 집행이 핵심이며 CPU 엔진에서 기본 시험 가능하다. |
| [REQ-04](../packs/PACK-BASELINE/checks/REQ-04.yaml) | 불필요 | 외부 입력이 인가에 쓰는 신원을 사칭하지 못하는가? | 전달 신원·header 정규화와 인가에 소비되는 값의 대조 시험이다. |
| [REQ-05](../packs/PACK-BASELINE/checks/REQ-05.yaml) | 조건부 | 호출자의 모델 사용권이 최종 선택 대상에도 적용되는가? | 기본 모델 권한은 CPU로 시험 가능하나 활성 GPU LoRA·adapter의 최종 선택·실행까지 포함하면 GPU가 필요할 수 있다. |
| [REQ-06](../packs/PACK-BASELINE/checks/REQ-06.yaml) | 불필요 | 저장 결과의 조회·취소 권한을 객체별로 확인하는가? | 저장 객체 owner·조회·취소·체인 권한은 CPU 생성 요청으로 기본 시험 가능하다. native 회수는 LIF-10이다. |
| [REQ-07](../packs/PACK-BASELINE/checks/REQ-07.yaml) | 불필요 | template 적용 뒤 입력에도 필요한 제한이 유지되는가? | template 렌더 뒤 실제 tokenized 입력과 길이·내용 제한을 CPU에서 대조 가능하다. |
| [REQ-08](../packs/PACK-BASELINE/checks/REQ-08.yaml) | 조건부 | 재사용 token ID 입력도 필요한 검사를 거치는가? | 재사용 token ID 검사는 CPU로 가능하나 활성 GPU KV 전달·소비 경로까지 포함하면 GPU 시험이 필요하다. |
| [REQ-09](../packs/PACK-BASELINE/checks/REQ-09.yaml) | 조건부 | token ID와 입력 길이가 실제 모델·소비자의 유효 범위를 지키는가? | CPU에서 경계값 거부를 시험할 수 있으나 GPU runner·kernel의 실제 유효 범위·오류 후 정상성은 GPU에서 확인한다. |
| [REQ-10](../packs/PACK-BASELINE/checks/REQ-10.yaml) | 조건부 | sampling 값과 옵션 조합이 실제 실행 조건을 만족하는가? | API 옵션 검사는 CPU로 가능하나 GPU sampler·worker의 실제 조합 제한·생존은 해당 backend에서 확인한다. |
| [REQ-11](../packs/PACK-BASELINE/checks/REQ-11.yaml) | 불필요 | 동시 요청과 스트리밍 결과가 올바른 원 요청으로 반환되는가? | 동시·stream 응답의 연결·request ID·chunk 귀속은 CPU 엔진으로 기본 시험 가능하다. |
| [REQ-12](../packs/PACK-BASELINE/checks/REQ-12.yaml) | 불필요 | 취소·연결 종료 뒤 늦은 결과가 다른 요청이나 새 시도에 섞이지 않는가? | 취소 뒤 늦은 chunk·attempt의 귀속은 CPU 엔진으로 기본 시험 가능하다. 실제 자원 회수는 LIF-10이다. |
| [REQ-13](../packs/PACK-BASELINE/checks/REQ-13.yaml) | 불필요 | 응답 옵션과 오류가 독자에게 허용한 정보만 반환하는가? | 응답·오류의 비허용 필드와 표식 노출은 CPU 엔진에서도 시험 가능하다. |
| [REQ-14](../packs/PACK-BASELINE/checks/REQ-14.yaml) | 조건부 | 분산 처리의 중간 자료와 최종 출력이 원 요청에 연결되는가? | GPU TP·PP·PD가 선정되면 실제 rank·buffer·중간 자료 전달을 해당 구성에서 계측해야 한다. |
| [REQ-15](../packs/PACK-BASELINE/checks/REQ-15.yaml) | 불필요 | cache 공유 집단을 신뢰할 신원에서 결정하는가? | 신뢰 신원·salt와 cache 공유 집단의 대응은 CPU cache 경로에서 기본 시험 가능하다. |
| [REQ-16](../packs/PACK-BASELINE/checks/REQ-16.yaml) | 조건부 | cache key가 실제 재사용에 필요한 문맥을 구분하는가? | 기본 cache key는 CPU로 시험 가능하나 활성 GPU adapter·멀티모달·model generation의 재사용 분기는 해당 구성이 필요하다. |
| [REQ-17](../packs/PACK-BASELINE/checks/REQ-17.yaml) | 조건부 | cache 시간 차로 비공개 정보가 허용 범위를 넘어 드러나지 않는가? | hit 필드 노출은 CPU로 확인 가능하나 GPU 서비스의 시간 차·분포에 대한 기준은 해당 하드웨어·부하에서 측정한다. |
| [REQ-18](../packs/PACK-BASELINE/checks/REQ-18.yaml) | 조건부 | 배치 자리 이동·재사용 시 이전 요청의 제한 상태가 남지 않는가? | 선정 V1/V2 runner의 slot·mask·count·staged write가 GPU 구현이면 CPU 대응 경로로 대체할 수 없다. |
| [REQ-19](../packs/PACK-BASELINE/checks/REQ-19.yaml) | 필수 | 선정 kernel의 접근 범위와 초기화가 유효 입력·buffer 범위를 지키는가? | 원문이 활성 GPU·kernel·dtype·shape 및 device buffer 범위·초기화를 직접 시험 대상으로 정한다. |
| [REQ-20](../packs/PACK-BASELINE/checks/REQ-20.yaml) | 필수 | CUDA Graph 재실행에서 현재 입력을 사용하고 출력 소비 전에 buffer를 잘못 덮어쓰지 않는가? | CUDA Graph capture·replay·stream event·device buffer 수명 시험이다. |
| [REQ-21](../packs/PACK-BASELINE/checks/REQ-21.yaml) | 필수 | TP graph 통신 buffer의 참조가 등록·종료·재기동 과정에서 유효한가? | 선정 GPU TP graph·communicator의 native buffer·IPC handle 등록·해제·재등록 시험이다. |
| [PLT-01](../packs/PACK-BASELINE/checks/PLT-01.yaml) | 조건부 | 엔진·worker가 불필요한 호스트·파일·장치 권한을 갖지 않는가? | UID·capability·파일 권한은 CPU로 가능하나 GPU device 접근 통제와 정상 GPU 로딩·TP 확인은 GPU가 필요하다. |
| [PLT-02](../packs/PACK-BASELINE/checks/PLT-02.yaml) | 조건부 | 로컬 IPC 접점에 허용한 프로세스만 접근하는가? | 일반 socket·SHM은 CPU로 가능하나 실제 GPU worker·CUDA IPC 구성이 선정되면 해당 자원이 필요하다. |
| [PLT-03](../packs/PACK-BASELINE/checks/PLT-03.yaml) | 불필요 | 취득 주체의 자격이 허용 저장소·객체에 한정되는가? | 취득 계정의 저장소·객체 접근권과 실제 대리 identity를 시험한다. |
| [PLT-04](../packs/PACK-BASELINE/checks/PLT-04.yaml) | 조건부 | worker에 불필요한 자격이 전달되지 않는가? | 환경변수·mount 전달은 CPU로 가능하나 GPU TP·Ray rank별 유효 자격과 정상 실행은 해당 구성에서 확인한다. |
| [PLT-05](../packs/PACK-BASELINE/checks/PLT-05.yaml) | 불필요 | 자격 만료·누락 시 비승인 대체 접근으로 이어지지 않는가? | 선정 SDK의 자격 만료·누락과 provider·anonymous·cache fallback 시험이다. |
| [PLT-06](../packs/PACK-BASELINE/checks/PLT-06.yaml) | 조건부 | core·worker가 공유하는 실제 신뢰 범위와 외부 접근 제한을 확인했는가? | UID·namespace·직렬화 사전 검토는 CPU로 가능하나 실제 GPU core·TP worker 경계는 해당 구성에서 확인한다. |
| [PLT-07](../packs/PACK-BASELINE/checks/PLT-07.yaml) | 불필요 | 외부 송신이 허용 목적지와 작업에 한정되는가? | 목적지·작업별 송신 통제는 CPU 프로세스·시험 목적지로 기본 검증 가능하다. |
| [PLT-08](../packs/PACK-BASELINE/checks/PLT-08.yaml) | 조건부 | 각 내부 통신 구간이 정한 상대 확인·기밀성·무결성을 충족하는가? | CPU IPC 검토와 GPU TP·NCCL 등 실제 transport·peer 보호 검증을 구분한다. |
| [PLT-09](../packs/PACK-BASELINE/checks/PLT-09.yaml) | 불필요 | 엔진부터 collector와 최종 sink까지 필요한 전송 보호가 이어지는가? | 엔진·collector·sink의 SDK·protocol·TLS·종단 보호는 CPU 이벤트로 시험 가능하다. |
| [PLT-10](../packs/PACK-BASELINE/checks/PLT-10.yaml) | 불필요 | 철회 이후 신규 접근이 정한 기한 안에 거부되는가? | 자격 철회 후 신규 접근 거부와 기한을 측정한다. |
| [PLT-11](../packs/PACK-BASELINE/checks/PLT-11.yaml) | 불필요 | 기존 session·handle의 접근도 철회 정책에 맞게 제한되는가? | 기존 session·handle과 신규 접근에 대한 철회 정책을 시험한다. |
| [PLT-12](../packs/PACK-BASELINE/checks/PLT-12.yaml) | 불필요 | 폐기된 자격 사본을 재사용할 수 없는가? | 자격 사본 위치·폐기·옛 자격의 접근 거부를 시험한다. |
| [LIF-01](../packs/PACK-BASELINE/checks/LIF-01.yaml) | 조건부 | 모델 취득·해석·변환·로딩 비용이 단계별·누적 예산을 지키는가? | 취득·해석 비용은 CPU로 가능하나 GPU 로딩·compile·GPU 메모리 예산은 해당 자원에서 측정한다. |
| [LIF-02](../packs/PACK-BASELINE/checks/LIF-02.yaml) | 불필요 | 실패한 취득의 반복·재시도가 비용을 무한 누적하지 않는가? | 취득 retry·backoff·누적 다운로드·부분 파일 정리는 CPU 작업이다. |
| [LIF-03](../packs/PACK-BASELINE/checks/LIF-03.yaml) | 조건부 | 요청 비용과 동시 부하를 제한하며 정상 요청의 처리 기회를 유지하는가? | admission·queue는 CPU로 시험 가능하나 GPU 서비스의 지연·처리율·공정성 기준은 대표 GPU 부하에서 측정한다. |
| [LIF-04](../packs/PACK-BASELINE/checks/LIF-04.yaml) | 불필요 | 느린 수신자와 진단 buffering이 자원을 과도하게 점유하지 않는가? | SSE 느린 수신자·host buffer·backpressure는 CPU stream 생성으로 기본 시험 가능하다. |
| [LIF-05](../packs/PACK-BASELINE/checks/LIF-05.yaml) | 조건부 | 재배포 중 이전 요청과 새 모델·설정이 잘못 섞이지 않는가? | CPU로 세대·라우팅 통제를 시험 가능하나 GPU 모델 drain·재적재·준비 경계까지 검증하려면 GPU가 필요하다. |
| [LIF-06](../packs/PACK-BASELINE/checks/LIF-06.yaml) | 조건부 | live 변경이나 rolling 중에도 요청의 모델·상태 일관성을 지키는가? | GPU live weights·LoRA·rolling이 활성화되면 실제 weights·cache·요청 세대의 일관성을 확인해야 한다. |
| [LIF-07](../packs/PACK-BASELINE/checks/LIF-07.yaml) | 조건부 | 필요한 worker와 실행 준비를 확인한 뒤 요청을 받는가? | GPU TP rank·graph·backend의 실제 준비와 정상 추론은 GPU 준비 경로에서 계측한다. |
| [LIF-08](../packs/PACK-BASELINE/checks/LIF-08.yaml) | 조건부 | rank의 예외·사망·지연·통신 정지를 각각 감지하고 전달하는가? | 단일 CPU worker 실패는 사전 검증이며 GPU TP rank·통신 정지의 감지·전파는 해당 구성이 필요하다. |
| [LIF-09](../packs/PACK-BASELINE/checks/LIF-09.yaml) | 조건부 | 초기화·모델 준비의 부분 실패가 성공으로 처리되지 않는가? | 파일 누락은 CPU로 시험 가능하나 GPU 초기화·rank 준비의 부분 실패 분기는 실제 GPU 실행 경로에서 확인한다. |
| [LIF-10](../packs/PACK-BASELINE/checks/LIF-10.yaml) | 조건부 | 취소·연결 종료 뒤 실제 작업과 참조가 정한 기한에 종료되는가? | CPU의 abort 전파만으로 GPU 실행·native 참조·device 자원 회수 완료를 증명할 수 없다. |
| [LIF-11](../packs/PACK-BASELINE/checks/LIF-11.yaml) | 조건부 | 실패한 worker·통신·분산 자원이 회수되고 다음 기동이 가능한가? | CPU process·SHM 회수와 GPU TP·NCCL·device 자원의 회수·재기동은 별도 관측이 필요하다. |
| [LIF-12](../packs/PACK-BASELINE/checks/LIF-12.yaml) | 조건부 | 재시도 전후 이전 작업의 잔존과 중복 비용이 통제되는가? | retry 설정·attempt 연결은 CPU로 가능하나 GPU 작업의 잔존·중복 연산·비용 회수는 GPU에서 관측한다. |
| [LIF-13](../packs/PACK-BASELINE/checks/LIF-13.yaml) | 필수 | daemon 전체 release 이후 다른 소비자의 다음 취득·fallback과 기존 참조가 허용한 상태를 지키는가? | 원문이 copy 완료와 이미 열린 CUDA IPC 참조의 native 효과·release·fallback을 분리 계측하도록 한다. |
| [LIF-14](../packs/PACK-BASELINE/checks/LIF-14.yaml) | 조건부 | profiler 종료가 실제 수집·자원 종료와 맞는가? | CPU profiler는 CPU로 시험 가능하나 선정 CUDA profiler의 수집·종료·자원 완료에는 GPU가 필요하다. |
| [LIF-15](../packs/PACK-BASELINE/checks/LIF-15.yaml) | 불필요 | rollback 대상이 현재 승인 기준을 만족하는가? | 현재 승인 목록과 rollback 대상에 대한 배포 gate·실제 식별정보 대조가 핵심이다. |
| [LIF-16](../packs/PACK-BASELINE/checks/LIF-16.yaml) | 조건부 | 복원 자료의 변조와 잔존 상태가 복구 결과에 섞이지 않는가? | 일반 백업 승인·거부는 CPU로 가능하나 GPU 모델 복구·선택 snapshot·oracle·gate 순서는 지원 GPU 구성에서 검증한다. |
| [OBS-01](../packs/PACK-BASELINE/checks/OBS-01.yaml) | 불필요 | 필수 접근·관리·승인 사건을 주체·대상·실행과 연결할 수 있는가? | 필수 사건의 주체·대상·실행·정책·승인 연결은 CPU 사건과 실제 기록 시스템으로 시험 가능하다. |
| [OBS-02](../packs/PACK-BASELINE/checks/OBS-02.yaml) | 조건부 | 준비·실패·종료 기록이 실제 상태를 올바르게 나타내는가? | API·로그 대조는 CPU로 가능하나 GPU worker의 실제 준비·실행·종료 완료와의 일치는 GPU 관측이 필요하다. |
| [OBS-03](../packs/PACK-BASELINE/checks/OBS-03.yaml) | 조건부 | 로그·debug·trace·덤프가 허용한 정보만 수집하는가? | 일반 로그 표식은 CPU로 가능하나 GPU worker 예외 dump·선택 CUDA trace·profiler 자료는 해당 경로를 발생시켜야 한다. |
| [OBS-04](../packs/PACK-BASELINE/checks/OBS-04.yaml) | 불필요 | 금지한 usage 보고가 실제로 비활성 상태를 유지하는가? | 새 프로세스의 usage 판단·생성·송신 비활성화는 CPU 실행으로 기본 시험 가능하다. |
| [OBS-05](../packs/PACK-BASELINE/checks/OBS-05.yaml) | 불필요 | 허용한 usage 보고가 필드·목적지 범위를 지키는가? | usage schema·payload·목적지·수신 범위는 CPU 시험 프로세스와 sink로 기본 검증 가능하다. |
| [OBS-06](../packs/PACK-BASELINE/checks/OBS-06.yaml) | 불필요 | 기록과 진단 사본을 허용된 독자만 조회·반출하는가? | 원본·collector·backup 기록의 독자별 조회·반출 권한 시험이다. |
| [OBS-07](../packs/PACK-BASELINE/checks/OBS-07.yaml) | 조건부 | 진단 자료 읽기권만으로 재실행할 수 없는가? | replay 비사용·reader 권한은 CPU로 확인 가능하나 선정 replay consumer가 GPU 실행을 수행하면 허용 실행 대조에 GPU가 필요하다. |
| [OBS-08](../packs/PACK-BASELINE/checks/OBS-08.yaml) | 불필요 | 재실행이 변조한 진단 자료를 비허용 방식으로 소비하지 않는가? | replay parser의 출처·digest·reader 검증과 비허용 부수 동작은 CPU 무해 fixture로 사전·기본 시험 가능하다. |
| [OBS-09](../packs/PACK-BASELINE/checks/OBS-09.yaml) | 불필요 | 요청 입력이 로그의 신원·대상·상태를 위조하지 못하는가? | 입력에 의한 로그 사건 경계·신원·상태 위조를 CPU 요청·로그로 시험 가능하다. |
| [OBS-10](../packs/PACK-BASELINE/checks/OBS-10.yaml) | 불필요 | 기록의 무단 변경·삭제를 제한하고 확인할 수 있는가? | 기록 저장소의 변경·삭제·대체 통제와 독립 감사 시험이다. |
| [OBS-11](../packs/PACK-BASELINE/checks/OBS-11.yaml) | 불필요 | 수집 중단·누락·시간 불확실성을 정상 결과로 처리하지 않는가? | 합성 사건과 collector 중단·쓰기 거부·누락 경보를 시험한다. |
| [OBS-12](../packs/PACK-BASELINE/checks/OBS-12.yaml) | 불필요 | 로그·usage·진단의 원본과 사본이 보존·삭제 범위를 지키는가? | 기록 사본의 만료·삭제·접근·복구 가능성을 저장 시스템에서 시험한다. |

## 집계 대상 고정

- 원천: `reference/framework/data/checklist.json`의 5개 assessment 배열과 현재 `packs/PACK-BASELINE/checks/*.yaml`의 활성 CheckDef 77개. 83개 시나리오 수와 혼합하지 않았다.
- 77개 CheckDef ID의 누락·중복 없음과 원천 질문의 일치를 확인했다. 분류는 실측 결과가 아니라 원문에 대한 설계 판단이다.
- 체크리스트 원천 SHA-256: `8c24b31ace48a695271766b2adc6360b8570590ee5d4485e451a50c48eaf4671`
- CheckDef 묶음 SHA-256: `687c6b715b1316b6ce847684b4d030a515f236b72e1cb64fec3796a15961ee87`
- 묶음 해시 계산: 파일명 순으로 `파일명:파일bytes_SHA256\n`을 연결한 UTF-8 bytes의 SHA-256.
