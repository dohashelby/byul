# GPU 미기동 준비 보강 — 2026-11-09

기준은 PR232의 고정 head `8bcb8fa1`이다. 기존 세션을 재사용해 B4 로컬 사전검증, REQ08/09/10 기록 인계, SUP16 정적 실측 준비를 분리 구현했다. 새 GPU·엔진·모델·SSM·HTTP 시험·다운로드는 실행하지 않았다. 준비 도구가 읽는 로컬 파일과 Git 검사는 실제로 수행하며 실제 GPU 신원·승인·관측으로 올리지 않는다.

| 묶음 | 구현·확인 범위 | 검증 | 남은 실제 결합 |
|---|---|---|---|
| B4 | 선택적 로컬 root 안의 일반 JSON 파일·bytes SHA·sealed ENV content SHA·선언 결합·SUP06 같은 role 쌍 검사 | 기존13+신규21=34통과, pytest10.71초/부모11.641초 | 전체 prepared 자산·target/제어 절차·생산자 등록·현재 runtime·권한·TP2 |
| REQ08/09/10 | 수집 wrapper의 원 record bytes 추출, 독립 ENV/target/prepared/session·case→request 선언과 기존 consumer 인계 | 원 producer→준비→기존 consumer 및 대표 factory/_finish→EVD/Store/judge의 CPU 계약31통과, 8.91초/부모11.907초. 실제 준비 CLI1회 exit0/0.890초 | 실제 B HTTP 요청과 callback의 의미상 대응·수집/소비 시점·현재 환경·Ready. core run_plan/RPT 실행은 이번 범위 아님 |
| SUP16 | 고정 pair/helper/stage/prepare/source-owner/원 절차 자산과 명령 인계, 실제 정상·부분 파일 사본 SHA 검사 | 새 직접17통과, 6.06초/부모10.048초. 실제 정적 CLI exit0/5.532초 | 실제 양 role ENV/target/prepared/session·정책창·Pod/PID/start·원기한 Ready·현재 worker/native 귀속 |

## 사용하는 위치

- [B4 도구와 상세](B12_B4_INTEGRATION.md#b4-로컬-입력-사전검증-보강--2026-11-09): `update/tools/b12_tp2_inputs.py --verify-inputs-root <로컬-입력-루트>`를 기존 prepare 명령에 선택적으로 붙인다. 옵션이 없으면 기존 포인터 선언만 확인한다. 오류 진단은 `local-verification.json`과 manifest에 연결하며 FAILED는 CLI2, 미확정은 INCOMPLETE다.
- [REQ 인계 상세](https://github.com/dohashelby/byul/blob/3c611e214044460d5929e8fb0c3a615265425da0/update/docs/b-work/B12_S5_CONSUMERS_20261108.md): `update/tools/b_runner_inputs.py --inputs <입력.json> --inputs-sha256 <파일SHA> --output <새-디렉터리>`. 독립 입력과 원 record를 pin해 `runner-input.json`으로 인계한다. raw record를 재직렬화하거나 기록에서 기대 scope/매핑을 생성하지 않는다.
- [SUP16 인계 상세](../preparation/B3_SUP15_SUP16_RECORDS.md): `update/tools/gpu_b_sup16_prepare.py --help`의 명시 입력을 고정한다. 기존 private 준비 자산4종은 실제 bytes와 정확 SHA를 받아 사용하며 공개 helper snapshot은 동일 bytes로 준비한다. 현재 도구는 argv를 만들고 해당 writer/엔진을 실행하지 않는다.
- [고정 실행 근거](preparation-validation-20261109.json): 각 source·원 명령/시각·JUnit/receipt·검토·실제 정적 CLI의 원 자료를 정확 SHA로 연결한다. 큰 model/원 archive/키는 PR에 복사하지 않는다.

모든 출력은 새 디렉터리를 요구하고 기존 결과를 지우거나 덮어쓰지 않는다. B4는 bytes 검증과 객체 Ref/content hash를 구분한다. REQ의 case→request SHA가 존재하고 순서가 맞아도 의미상 HTTP 대응 인증은 아니다. `native_request_mapping_verified/runtime_link_complete/gpu_ready`는 false다. 기존 headers={} 전송과 X-Request-Id byul-cb 계약·판정·생산자 등록·팩을 바꾸지 않는다.

SUP16은 pair source `ed961609…`, helper `2485ba44…`, stage `c61b7059…`, prepare_pair `c1aff210…`, source owner `fb12dbe8…`, procedure `e48fefc2…`, native17 sensor `e32c0a53…`를 고정한다. 양 role의 record producer는 같은 bytes이고 실제 정상/부분 사본 검사는 S1의 정적 CLI에서만 했다. root 통합 CLI는 새 Git source/자산 결합만 확인해 6.718초/exit0이며 모델 인자를 생략했다. 두 실행의 모델 확인 범위를 합쳐 새 모델 실행으로 쓰지 않는다. 원 procedure의 과거 source/writer argv는 실행 명령으로 승격하지 않으며, asset90초·collection120초·Pod600초와 기존 owned cleanup 참조를 유지한다.

B4 `ready_for_execution/execution_authorized/runtime_binding_verified/prepared_assets_verified`, REQ 실제 매핑/연결 준비, SUP16 현재 worker/producer 인증은 미확인 또는 false로 남는다. 43ID state·원 FAIL/UNKNOWN·Ref, lab2/41·기존한정25/제품증거18·이전실제로컬14 분류는 유지한다. 이번 테스트 수와 완료 ID를 합산하지 않는다.

## REQ 오류 출력의 추가 보완

통합·독립 검토에서 잘못된 wrapper 항목과 ENV ValidationError의 입력 본문이 stderr로 복사되는 두 경계를 확인했다. 각각 무해 marker의 원2실패를 보존하고 고정 진단 코드와 원 예외 종류만 남기도록 수정했다. 두 수정 경계는 각각2통과(2.73초/부모4.203초, 1.77초/부모3.000초)다. 기존31개를 반복하지 않았으며 추가4회 중 기존사례1개가 중복돼 최종 고유 통과 사례는34개다. 같은 입력·출력·정책·consumer 의미는 유지한다.

첫 추가 baseline의 하네스 metadata 경로 치환 오류와 동일 bytes 재작성은 별도 note에 남겼다. 원 v1 영수증 및 고정 argv/environment/JUnit/audit/source snapshot/stdout/stderr의 SHA가 모두 같음을 확인한 뒤 후속을 새 경로로 분리했다. 이 기록은 제품 결함·검증 성공·실제 GPU 증거로 합산하지 않는다.

고정 B4·SUP16·REQ 최종 변경은 세션6이 읽기 전용으로 독립 검토했다. 원 명령/JUnit/파일 pin·Git bytes·정적 CLI 및 REQ Store9객체의 정확 Ref/content hash를 대조했으며 범위내 미해결 구체 결함과 추가 시험 요청은0이다. 기존 모델·전체 suite 재실행은 없었고, 이번 새 PR head의 CI는 게시 후 별도 확인한다.
