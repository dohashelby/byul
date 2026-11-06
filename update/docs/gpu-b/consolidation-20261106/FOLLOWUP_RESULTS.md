# 승인 후속 작업 — 검증된 반환 결과

이 문서는 기존 세션1·2·4·5·6에 배정한 후속 업무에서 고정·확인된 결과를 PR 검토용으로 인계한다. 실행 중 준비 작업이나 송신·접수를 시험 완료로 표시하지 않는다. 이전 전체 범위와 한정 마감 판정은 [TEAM_HANDOFF.md](TEAM_HANDOFF.md)를 따른다.

## 과거 원자료5개 — 자료 확보 대기 확정

세션4가 `history5-known-pointers-delta-v1`로 기존 포인터와 알려진 보관 위치만 대조했다. 대상5ID·산출물 manifest 및 원 검토 참조 SHA를 확인했고, 게시 담당도 고정 인계의 SHA와 대상 집합을 대조했다. 새 원자료 확보0·새 실행0·새 원자료 검증/복원/재판정0이다.

| 대상 | 확인된 범위 | 필요한 반환물 |
| --- | --- | --- |
| LIF-08·LIF-12·LIF-16, OBS-02 | 당시 CPU 실행 보고와 보고 source·engine source 구분. 원 실행 경로·명령·receipt는 미확인 | 당시 실행 source·이미지·명령·세대/receipt, 원 scenario/archive·Store/제품 Ref·저장 팩 |
| SUP-08 | 당시 GPU 보고의 파일 경로·보고 SHA와 일부 정확 Ref 보존. 실물과 전체 증거 연결은 미확인 | 원 archive·manifest·receipt·실행 입력/환경·전체 Ref·Store·저장 팩 |

기존에 알려진 보관 root48개 바로 아래의 명시 basename5개, 총240개 직접 경로에서 새 원본은 확인되지 않았다. 하위 트리를 탐색하지 않았으며 이전 광역 파일명 검색을 반복하지 않았다. 이 결과는 다른 호스트·하위 경로·일반 이름 archive나 Store에 원본이 없다는 증거가 아니다.

보유자와 접근 가능한 공유 경로는 사용자 확인에서도 미확인이다. 회수 가능 회신이 없으므로 위5ID는 과거 보고 있음/원본 미확인·자료 반환 대기로 유지한다. 원본 소실·과거 미실행·재실험 필요성을 확정하지 않는다. 자료가 실제 반환될 때만 정확 경로·SHA·기존 검증 범위를 세션6에 연결한다. 동일 가용성의 중복 조사는 종료하며 이 대기는 다른 CPU 준비·수집 경로 검토의 선행 조건이 아니다.

고정 인계 참조는 프로젝트 상대 경로 `worktrees/byul-gpu-b-history-6-20261106/out/history5-known-pointers-delta-v1/history5-known-pointers-delta-v1.json`, SHA256 `65f27b3c12ced45cdc3d904c0032d7bbf06746f187eb01fea5791106c369bdbb`이다. 원자료와 로컬 절대 경로·인프라 식별자는 게시하지 않는다.

사용자 확인의 추가 인계는 같은 디렉터리의 `owner-unconfirmed-addendum-v1.json`, SHA256 `230e1d732c04f59bd51dc305585f8fac20f672c4089995092182755f4f8a4cce`다. 공개 요약 `PR용_과거5_자료대기.md`의 SHA256 `93de233405b0ff6109bed65e643fed825e7b4b37fbe44f6c1ae93f28cb30b0ed`도 대조했다. 기존 base·manifest는 변경하지 않았다.

## 전송 조회 변경 — 로컬 컴포넌트 검토 완료

세션2가 반복적인 명령 이력 디렉터리 탐색을 호출 안에서 만든 terminal index로 바꿨다. 세션6이 `S6-TRANSPORT-TERMINAL-INDEX-DELTA-20261106-02 / component-review-v1`로 단일 함수 외 AST 동일, 실제 terminal producer와의 연결, 미수락·불확실·미종료 처리와 기존 기한·보호·정리 계약 보존을 확인했다. 이 컴포넌트 범위의 차단 결함과 필수 추가 입력은 없다.

실제 로컬 metadata 이력2,138개의 한 snapshot에서 구 버전9.523248초·새 버전0.131032초였고 미종료 결과0행과 그 해시, metadata 전후 상태가 일치했다. 기존 합성 입력의 glob 호출204→3 근거도 재사용했다. 이는 한 로컬 비교 결과이며 반복 성능 분포·실제 AWS 전송 속도·GB09 회수 성공을 입증하지 않는다. 기존 CPU fixture를 검토자가 다시 실행하지 않았다.

원 packet의 CPU argv가 Windows 표시 경로였던 문제는 원 Linux 실행 기록을 가리키는 후속 정정으로 해소했다. 원 Python 세부 버전은 미기록으로 남겼고 현재 값을 소급하지 않았다. 원 packet과 과거 실패는 변경하지 않았다.

고정 adapter 전체 bytes SHA256은 `4bedb048ccce521673762671e8e3b802409a6e9d33a7d8665daf032a20e52c75`다. [검토용 함수 발췌](implementation-snapshots/transport_terminal_index_function.py.txt)는 원 함수의 AST 동일성을 확인한 참조이며 독립 실행기나 설치된 운영 코드가 아니다. 실제 private 배치·import origin·전체 운영 packet·현재 자원/비용 연결은 별도 검토가 필요하다.

세션6 고정 검토의 프로젝트 상대 경로는 `worktrees/byul-gpu-b-next-verify-20261106/out/reviews/transport-terminal-index-delta-20261106-v1/component-review-v1.json`, SHA256 `3055918cef1d1c6cdf250fa9136c9a65b1309ee144c5f8c81fbd82f6de557082`다. 기존 CHUNK12000·GET4/PUT1·전송900초·정지180초·2시간 보호 조건을 변경하지 않았다.

## 10ID의 지원·반환물 인계

세션1이 LIF-11·SUP-03·PLT-02·LIF-06·SUP-09·SUP-10·SUP-11·REQ-09·REQ-10·SUP-06을 분기별로 ①기존 지원으로 전용 준비 가능 ②공통 담당의 정확한 지원 반환 필요 ③실제 모델·정책·topology 입력 필요로 구분했다. [DEPENDENCY_RETURNS.md](DEPENDENCY_RETURNS.md)에 파일·함수, 필요한 실제 출력, 기존 consumer 영향과 검증 입력을 연결한다. 항목 전체를 일괄 공통 대기로 분류하지 않는다.

게시 담당은 고정 원문 SHA와 10고유ID 및 공개 범위를 대조했다. 이는 반환 계약 분석의 인계이며 새 코드 검토·실제 모델 실행·제품 완료의 판정은 아니다. LIF-11과 SUP-03 새 준비 패킷의 CPU 결과는 해당 검토가 반환된 범위에서 별도 반영한다. 실제 입력 미결합 상태를 실행 가능으로 올리지 않는다.

원 인계 프로젝트 상대 경로는 `guard-output/gpu-stage1-3sessions/prepare/S1-followup-product-connections/v1/dependency-returns.md`, SHA256 `7382f6cb16ead5437aa6538cdc455639b5250aa26308e0b5ebce6e5670180ad7`이다. 새 원본·공통 지원 반환·실제 환경 입력이 없는 분기의 동일 시험은 반복하지 않는다.

## 추가 결과의 확인 기준

## 전송 전용 실행본 결합 — 사전 검토 완료

세션6이 `S6-EXACT-GB09-TRANSPORT-V3-OPERATING-BINDING-20261106-01 / fixed-operating-binding-v1`을 검토했다. 기존 컴포넌트 검토를 재사용하고, 실제 배치6파일의 신원·권한·SHA와 원 import명에 새 bytes가 연결된 사실, 현재 actor에 맞춘 invoker·보조도구, 시각별 자원 조회와 비용·기한 연결을 확인했다. 이 고정 결합 범위의 차단 결함과 추가 필수 입력은 없다.

기존 driver/helper와 archive 신원, 불확실한 수락의 후속 실행 차단, 전송900초·정지180초·2시간 보호 조건은 유지했다. 선정 범위는 원GB09의 정확한 읽기·회수이며 새 모델·worker 실험이 아니다. 사전 검토 자체로 실제 기동·전체 회수·전송 장애 해결·새 제품 결과를 인정하지 않는다. 실제 실행 결과는 별도 원자료 검증에서 연결한다.

고정 검토의 프로젝트 상대 경로는 `worktrees/byul-gpu-b-next-verify-20261106/out/reviews/transport-v3-operating-binding-20261106-v1/operating-binding-review-v1.json`, SHA256 `90a68d4abdd416afebb34c37235550d63a63d2aca5673a470fd45e99c8b99146`이다. 검토 시각은 2026-11-06 22:03 KST이며 이후 현재 자원·미래 성공을 보증하지 않는다.

## 기동 후 PID 신원 검사 — 최소 변경 검토 완료

기동 수락과 원 시작 프로세스의 정상 종료 뒤, 읽기 도구의 PID 숫자 존재 검사가 AWS 조회 전에 중단됐다. 원 실패는 보존하고, 원 actor의 positive terminal과 정확한 실행 argv를 결합해 동일 actor·부재·다른 프로세스를 구분하도록 좁게 보완했다. collect-only 경로는 새 start를 허용하지 않으며 기존 freeze·배타 실행·불확실성 차단·기한을 유지한다.

세션6은 22:12 KST에 해당 변경을 검토해 차단 결함과 필수 추가 입력이 없음을 반환했다. 22:10:51 KST까지의 후속 조회에서 running·SSM Online·활성 명령0이 확인된 근거를 연결했으며 이후 현재 상태의 보증으로 사용하지 않는다. 같은 기동을 재사용하며 원 프로세스 starttick 미수집 값을 새 값으로 채우지 않았다.

검토 참조는 `worktrees/byul-gpu-b-next-verify-20261106/out/reviews/transport-v3-poststart-identity-20261106-v2/identity-review-v2.json`, SHA256 `af91a731fe397c786168bde63d7ae79b6aa65563a85ff59417dcd40fee0d944b`다. 검토 자체는 회수 성공이나 새 제품 판정이 아니다.

## SUP-05 관측 보존 — F1 수정 검토 완료

첫 관측기 고정본에서 보조 스레드의 FD 닫기 오류가 상위 close 결과·worker 종료 오류에 전달되지 않는 결함이 발견돼 실제 모델 실행을 보류했다. 수정v2는 각 FD 오류를 누적하고 다른 FD 정리를 계속하며, 상태가 불명확한 FD 번호는 다시 닫지 않는다. 기존 worker 소비자에 errors·FD_release_verified·unconfirmed_FD_releases를 반환한다. 원 v1 차단·재현 기록은 보존한다.

세션5의 새 FD 오류 계약5개와 세션6의 수정v2 독립 pipe 오류 주입에서 오류 반환·다른 FD 정리·재close 없음이 확인됐다. 기존12·9·11 계약과 원 v1 검증은 재실행하지 않았다. worker/native/image/model·90초·2CPU/4GiB·정상 준비 선행 조건·정리 계약은 유지했다. 이 결과는 stdlib 관측기 검증이며 실제 모델 준비 성공이나 제품 결과가 아니다.

관측기 전체 bytes SHA256은 `44cf7c6cc7a37aa697748690a1e68e6fcbe83e927d469c35c3fb27914273c05b`다. [검토용 FD 정리 메서드 발췌](implementation-snapshots/sup05_observer_release_method.py.txt)는 원 메서드 AST 동일성을 확인한 참조이며 독립 실행기·운영 설치 완료를 뜻하지 않는다.

세션6 고정 검토 참조는 `worktrees/byul-gpu-b-next-verify-20261106/out/reviews/sup05-preservation-delta-20261106-v1/preservation-review-v2.json`, SHA256 `1bb68051b16902db5ec90e068bc2d4aeb41aae2d4b3ebfa50f50a7d4ca8351ed`다. 해당 고정 변경의 실행 전 차단 결함·추가 필수 입력은 없으며, 실제 정상 역할 실행과 원자료·정리 결과는 별도 검증한다.

## GB09 원본 전체 회수 — 무결성 검증 완료

2026-11-06 22:21:25 KST 세션6 검증에서, 새 전송v3로 반출한 정확한 원GB09 ZIP 2,203,771 bytes의 SHA256 `7ee9e4128cf5c2ff1e32feb23603a0ea10ee35b6c88fb0717370b32c60437673`를 확인했다. ZIP CRC674개·payload673개의 bytes/SHA·고유 manifest 집합·원 archive 생성 명령과 반출 receipt 결합·Linux 다운로드와 Windows 사본 일치가 확인됐다. 이는 이전 v2의 미반출·timeout 기록 이후 새로 확인한 전체 회수 성공이다. 과거 partial·timeout·정지 감시 실패 기록은 보존한다.

원 manifest 생성 시각은 같은 날18:58:25 KST다. 원 실험 source는 `3b3ff137b31b830403ccce1997dabf26319bac4f`이며 후속 회수용 부팅을 원 실험 부팅으로 바꾸지 않았다. manifest의 `normal_diagnostic_only=true`, `actual_product_Refs_created=false`, `whole_question_complete=false`를 유지한다. 이 무결성 검토만으로 writer/native 의미·정상 대조·factory→EVD→RES→RPT·정확 Ref 복원·저장 팩 재판정을 확인한 것은 아니다. 내부 의미 검토와 새 회수 건의 정지·자원·비용 마감 검증은 별도 반환을 기다린다.

고정 검토 참조는 `worktrees/byul-gpu-b-next-verify-20261106/out/reviews/transport-v3-actual-20261106/recovered-GB09-archive-integrity-v1.json`, SHA256 `5d5124b68eda36b0b34d987aecb9531dc6309d2ce53d6dfb7b813c51bb4e8943`다. 원 ZIP과 계정·인프라 식별자는 게시하지 않는다. 한 파일의 회수 성공을 모든 전송 장애 해결이나 SUP-16 제품 성공으로 확대하지 않으며 25개 한정 마감 제안·18개 제품 증거 필요 분류는 유지한다.

## LIF-11·SUP-03 준비 — 고정 코드의 정적 인계 검토

검토 대기를 줄이기 위해 게시 담당이 세션1의 두 고정 패킷을 정적으로 검토했다. source `3b3ff137b31b830403ccce1997dabf26319bac4f`, freeze 2개와 그 고정 파일25개의 bytes/SHA, Python AST, 원 CPU argv·종료0·로그 및 실제 consumer/factory 호출 경로를 대조했다. 새 시험·원 archive CRC·Ref 복원은 실행하지 않았다. 이 준비 코드의 정적 인계 범위에서 새 차단 결함은 발견하지 않았으며, 실제 운영 입력·신원·자원·기한·반출 패킷의 사전 검토를 대신하지 않는다.

| 항목 | 확인한 준비 범위 | 한계와 남은 입력 |
| --- | --- | --- |
| LIF-11 | 원 prepare_assets→load_prepared→입력 결합→등록 factory의 validate_policy. 원 EXAMPLE 오프라인 계약8개 통과 기록 재사용. 입력 hash·분기/profile·유효창·model mapping 대조 및 기존 출력 덮어쓰기 거부 | 실제 선정 worker/rank/PID/FD·ENV/target·정책/시나리오·장애/복구 입력 미제공. 실제 trigger·전역 자원 회수·후속 launch lineage 공통 출력도 필요 |
| SUP-03 | 실제 core/Store 인터페이스의 선정→EVD/RES/RPT→반출→복원·동일 as-of/저장 팩 재판정에 대한 EXAMPLE CPU 계약4개와 산출물 보존1개 통과 기록 재사용. adapter=vllm 보완과 원 실패 보존 확인 | CPU transport와 verify_source는 mock. 실제 source/runtime 소유권·ENV/target·반출 입력은 미검증. fixed template identity/정책 소속/API projection 범위이며 worker 소비·전체 공급 범위는 미확인 |

두 항목 모두 `GPU_READY=false`다. SUP-03의 EXAMPLE 결과는 functional/performance/security 모두 UNKNOWN, official=false, template_consumer_verified=false, supply_scope_complete=false다. 원 allowlist의 명시 빈 목록은 LAB DRAFT 입력이며 기업 승인이나 실제 비허용 파일 소비 FAIL로 바꾸지 않는다. native17의 weight 관측을 config/tokenizer/template 소비로 확대하지 않는다.

[선정 입력 준비 코드 참조](implementation-snapshots/lif11_selected_input_prepare.py.txt) SHA256 `92cf446ab47ed4da6700a9a70d0d48aee9cd42049eda68da1caa2faa315a0bb2`, [고정 template 제품 경로 코드 참조](implementation-snapshots/sup03_fixed_template_product.py.txt) SHA256 `bb92196f6a5f5d8edbe7ff448c366ea68d9ce08fdc0e2f9b68122f449a9520ed`는 원 bytes와 동일하다. 설치된 운영 코드나 source commit에 포함된 새 기능이라는 뜻은 아니다.

원 freeze의 프로젝트 상대 경로는 `worktrees/byul-gpu-b-prep-recovery-errors-20261104/out/lif11-selected-input-prep-v1/freeze.json` SHA256 `f5870a4431d267283f04cb3bb229d79c8e2e34cae263aab39b0c72b94e9496e4`, 같은 out의 `sup03-fixed-template-product-v1/freeze.json` SHA256 `384fbab8df616bce1831b280a128aedfdf2789fed9472cdcee1954fdff221682`다. SUP-03 v2는 verify_source의 UID/GID·mode 검사에 대한 문구만 정정했고 코드/시험은 재사용했다. 게시 담당 검토 참조는 `worktrees/byul-gpu-b-stage1-closeout-handoff-20261106/out/team-handoff/s1-fixed-preparation-static-review-v1.json`, SHA256 `4a42ecdb992c293d085a7bc016949b2a3cc3312f93ca42eded1ddebbbe48dcac`다.

## SUP-05 실제 CPU 정상 역할 1회 — 실패·원자료·정리 검증 완료

2026-11-06 22:33:24 KST 세션6이 승인된 CPU normal1회의 새57참조·실제 명령/실행 신원·원 예외·native 기록·입력 전후와 소유 정리 근거를 검증했다. 원 image·source·worker·모델과90초·2CPU/4GiB 조건을 유지했다. Engine 생성은58.089초에 반환하고 요청은58.102초에 제출됐으나 `engine.step`에서 `Unsupported CPU attention configuration: head_dim=4 isa=1 kv_cache_idx=0`로 실패했다. host/worker 종료값은1이며 역할과 정리는72.541초였다. 원90초 timeout과 다른 실패이고, 이전 timeout의 단일 원인이 해결됐다고 단정하지 않는다.

native 원문694행의 seq·prev SHA·정규 bytes·단조시각과 실행 신원을 확인했다. 1,206,057 bytes의 원문과 부분 mirror SHA가 같으며 A파일3,288 bytes의 실제 FD 읽기 반환은 관측됐다. 그러나 worker 배정·승인 파일 결합·tensor 적용은 미확인이다. END=PROCESS_EXIT는 정상 SEALED를 뜻하지 않는다. 정상 봉인·완전 수집·native admission은 성립하지 않았고 제품 EVD/RES/RPT는 생성되지 않았다. 복원할 새 제품 Ref나 저장 팩 재판정 대상도 없다.

작업 A파일의 hash 복구·engine 참조 소멸·관측 FD 정리 및 소유 컨테이너 제거/부재와 오류0을 확인했다. 이를 모든 내부 runtime 전역 자원의 회수 보증으로 확대하지 않는다. 정상 준비 실패에 따라 challenge·recovery·같은 입력 재시도는0회다. 별도 호환 입력 준비가 이 실패를 성공으로 바꾸지 않으며 CPU 진단을 GPU MEASURED/제품 PASS로 집계하지 않는다.

최종 검토 참조는 `worktrees/byul-gpu-b-next-verify-20261106/out/reviews/sup05-preservation-delta-20261106-v1/actual-normal-final-verification-v1.json`, SHA256 `a1da5a5b4c7dc9f14229224da99e017a1df09d49aadf134109099c40f4d13430`다. 이 고정 실패·정리 검증의 추가 필수 입력은 없으며 제품 검증 재개에는 설치판과 호환되는 별도 고정 모델 입력이 필요하다.

## 반환 결과의 추가 원칙

다음 반환은 고정 source/version·정확 산출물·필요 검토·실제 실행 환경과 한계를 함께 기록한다. CPU 계약이나 로컬 성능 관측을 실제 AWS 개선·GPU 제품 성공으로 올리지 않는다. 미검토 변경이나 실행되지 않은 계획은 별도로 유지한다. 기존 판정·기한·FAIL/UNKNOWN·원 source와 Ref를 변경하지 않는다.
