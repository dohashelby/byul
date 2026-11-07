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

## GB09 원실험 내부 의미 — 정상 응답과 제품 증거의 구분

게시 담당이 회수한 원GB09 내부의 실행 의미를 별도로 검토했다. 세션6의 전체 ZIP 무결성 검토는 재사용했고 CRC·전체 member SHA 검증을 반복하지 않았다. 원 source27개 pin의 expected=actual, 제품 source3b3와 sensor public commit db9527의 구분, native3파일 총1,210행의 정규 bytes·seq·prev SHA·단조시각·START session/sensor/settings 결합을 확인했다. 새로운 모델·worker 실행은0회다.

정상 API 응답은200·입력6/출력1 token으로 원 controller 응답과 일치하고 전후 같은 Pod 신원이 확인됐다. native의 OPEN/READ_RETURN/CLOSE 기록과 원 analyzer 후보1건을 대조했다. 250,500,968 bytes의 실제 FD 읽기 반환 hash는 선언된 EXAMPLE 기준값과 일치했다. GPU host process와 namespace PID/startticks도 연결되지만, 이 상관관계가 consumer 계약의 worker/rank 배정·승인 기준 권한·producer 인증·tensor/backend 적용 완료를 입증하지는 않는다.

worker 기록은 END=BOUND_REACHED로 끝났고 loader iterator 종료·operation 완결·decoder 완료·전체 inventory·적용 범위는 미확인이다. API 측 END=SEALED 및 정상 응답을 worker 전체 소비 완료로 해석하지 않는다. 원 analyzer의 security=UNKNOWN과 missing8개를 보존한다. 파일 읽기 FD 반환 증거와 `source_bound_eager_fd_read_unavailable` 등 더 강한 producer/consumer 보증이 남은 상태를 함께 인계한다.

원 진단 결과는 product_factory_executed=false·formal_product_metadata_generated=false·whole_question_complete=false다. archive 안에 제품 Store 경로가 없고 원 실행도 제품 증거 객체를 생성하지 않았으므로 새 EVD/RES/RPT의 정확 Ref 복원·저장 팩 재판정을 수행하지 않았다. 빈 대상을 완료 처리하거나 임의 객체를 만들지 않는다. 원 실험의 정상 UID/RV 삭제·NotFound·잔존 GPU process0 기록도 확인했으며 후속 회수용 부팅의 정지/비용 마감과 구분한다.

의미 검토 참조는 `worktrees/byul-gpu-b-stage1-closeout-handoff-20261106/out/team-handoff/recovered-GB09-semantic-review-v1.json`, SHA256 `8f2177abebff1351f92346634171f95c6dfe842c5a5adb71190c3d2c121243bf`다. 이로써 이번 회수 자료의 진단 의미와 한계는 확인했지만 SUP-16 제품 검증은 남는다. 재개에는 정상/변경/복구의 실제 factory 경로와 worker/rank·승인 기준 권한·실제 소비/적용의 지원 입력이 필요하다.

## REQ-08 registry 진단 — 실제 실행 전 오류 보완 검토

진단용 monitor thread의 시작 실패가 이미 생성된 원 Popen 객체의 반환을 가리고 미시작 thread join에서 추가 오류를 내는 결함을 세션6이 발견했다. 이전 고정본의 실행을 보류하고 원 child 제어는 유지한 채 진단 오류를 독립 누적하고 시작된 monitor만 join하도록 좁게 수정했다. 원 오류 주입·차단 기록은 보존한다.

세션6은 v5의 독립 stdlib child 오류 주입에서 원 Popen 반환·binary stdout/stderr·child 종료0·독립 진단 오류·미시작 join0·원 binding 및 소유 FD 회수를 확인했다. 기존 CPU5+finalizer4 및 세션5의 새4개 계약은 재사용했고 전체 suite·실제 모델·GPU를 검토자가 실행하지 않았다. local Docker endpoint 검사와 원 caller·registry pin·PIPE/원 예외·90초·85초 진단·2CPU/4GiB·정리 계약을 유지했다. 이 고정 delta의 차단 결함·필수 검토 잔여는0이며 실제 진단 결과는 별도로 검증한다.

검토 참조는 `worktrees/byul-gpu-b-next-verify-20261106/out/reviews/req08-registry-delta-20261106-v1/registry-diagnostic-review-v5.json`, SHA256 `51bf547289e6195fa166f6a506ad77e9acf83627dc83c1394c6fa1a27b7e5301`이다. 이 사전 검토를 REQ-08 제품 준비 성공이나 과거 child 종료값의 복구로 사용하지 않는다.

## GB09 회수 운영 마감 — 정지·비용·최종 증거 연결 검증 완료

2026-11-06 22:44:45 KST 세션6이 새 control archive의 CRC2,412개·payload2,411개와 원 파일 결합, 원 runtime·원 stop 감시 실패, 후속 실제 정지·비용 산술을 검증했다. 새 반출은 성공했지만 원 collect의 종료1·정상 stop1회·180초 감시 실패·원 final 부재는 유지한다. 정상 stop 재요청·Force·타이머 초기화는0회다.

22:22~22:23 KST의 각 조회에서는 대상 stopping/IP 있음·활성 SSM0·EIP0·보존 EBS440GiB였고, 이후22:24:55.647~22:24:57.407 KST EC2 조회에서 소유3대 stopped·공인IP0을 확인했다. 정확한 물리 정지 전이 시각은 미확정이고 이후 미래 상태를 이 조회로 보증하지 않는다. 비용은 보수적으로 기동 intent22:04:44.190부터 후속 정지 관측 끝까지 연산/IP를 계산해 닫았다.

장부의22:24:57.407 KST 기준 누적 추정은 `$53.08166739362498`, 예비비$5를 제외한 잔여는 `$41.91833260637502`다. 실청구 확인값은 아니다. 보존 EBS 비용은 계속되므로 이 금액을 이후 시각의 현재 비용으로 복사하지 않는다. 운영 담당의 최종 연결 시점에 실행 중 작업은0건이며 새기동·추가정지·자동화 재생성은 하지 않았다.

세션6 최종 검토 참조는 `worktrees/byul-gpu-b-next-verify-20261106/out/reviews/transport-v3-actual-20261106/current-control-final-review-v1.json`, SHA256 `28fa75d0d332d6808b7c666ae2d4d2c47079587727e03785cb9647b85318382d`다. 세션2의 최종 결합 참조는 `worktrees/byul-gpu-b-run-lif14-labels-20261105/out/SUP16-live-normal-v1-operator-20261105/transport-target-repair-v3/10-required-transport-GB09-recovery-final-link-v1.json`, SHA256 `f024ce04d4722e5d65e54f61154bd06fa12fc0b77065ffba1284a8362b8f3bb3`다. 기존 ZIP 무결성5d5124b6와 게시 담당 의미 검토8f2177ab를 재사용해 연결했으며 이번 회수·운영 마감의 추가 필수 입력은0이다.

이번 정확GB09의 전체 회수·무결성·진단 의미·운영 마감은 확인됐다. 원 실험의 제품 factory·EVD/RES/RPT는 미생성이며 SUP-16 제품 증거 보완과 43ID의25/18 분류는 유지한다. 다음 archive는 새 producer 명령·계획 basename·실제 생성 receipt에 맞춘 별도 binding이 필요하며 GB09 전용 결합을 그대로 사용하지 않는다.

## SUP-05 새 입력 준비 — public scope의 지원 대상 경계

새 호환 입력 준비에서 공개 `model_read_scope.build_scope`가 owned-Docker CPU target을 `KUBERNETES_CAPTURE_TARGET_INVALID`로 거부했다. 2026-11-06 22:52:12 KST의 첫 실패와 부분 준비 산출물은 보존했다. 게시 담당은 원 실패 receipt SHA와 고정 source3b3의 `model_read_scope.py`→Kubernetes capture의 `validate_target`→`kubernetes_binding.py` 연결을 대조했다. 이 공개 entry는 manifest를 읽기 전에 Kubernetes target 계약을 검사하므로 Docker 입력을 이 경로의 제품 연결 성공으로 처리할 수 없다.

공통 validator 수정이나 가짜 Kubernetes 신원 필드를 추가하지 않는다. 기존 native sensor `validate_scope`→`prepare_assets/load_prepared`로 이어지는 전용 CPU 준비는 별도 범위로 계속할 수 있지만 공개 build_scope·제품 factory·제품 Ref 완료를 대신하지 않는다. 새 정확 패킷 검토 전의 추가 엔진 실측은 보류하고 REQ-08 결과 검증은 독립 진행한다. 이 경계의 동일 실패는 반복하지 않는다.

Docker 대상을 동등한 제품 경로에 연결하려면 명시적으로 지원하는 owned-Docker native-scope entry의 반환 또는 실제 운영 담당의 Kubernetes runtime/신원 입력이 필요하다. 원 실패 참조는 `guard-output/gpu-stage1-3sessions/prepare/new5-SUP05-first-product/v1/compatible-input-v1/public-scope-failure-v1.json`, SHA256 `1366d9bd7cd1499732d358259c7e22b508ed58f026e66e640b57fea4bcd9ee15`다. 게시 담당의 정적 경계 검토는 `worktrees/byul-gpu-b-stage1-closeout-handoff-20261106/out/team-handoff/sup05-public-scope-boundary-review-v1.json`, SHA256 `6557801fb87cea83a8d03e3d6b7919c69182b5070f7bb2af6f70a643ee972b9d`다. 새 모델 전체 패킷·실제 실행 성공을 이 검토로 인정하지 않는다.

## SUP-03 전용 target 결합 — 오프라인 준비 정적 검토

기존 A target 도구는 A 전용 라벨·이름·모델 경로를 가정하므로 B의 실제 배포 값을 그대로 연결할 전용 결합기를 별도 준비했다. 공통 파일은 수정하지 않고 기존 `_binding`·`verify_owned_pod`·model_mapping·Kubernetes request-binding 생성자·원 SUP-03 `_target`을 재사용한다. 게시 담당은 고정 파일15개의 bytes/SHA와 AST, CPU7개 통과/실제 격리 CLI 종료0 기록 및 원 consumer 경로를 정적으로 대조했다. 새 시험·Kubernetes·모델·GPU 실행은0회다.

결합 입력은 실제 sealed ENV, 같은 실제 namespace와 서로 다른 engine/probe Pod의 원 JSON, model_mapping, 배포할 고정 template bytes 및 실제 context/run/container/port/version이다. 과거 SUP-16 engine 내부 준비 컨테이너를 별도 probe로 바꾸거나 미래 UID·PID·IP·시각을 만들지 않는다. 명시한 command/args의 template 경로·readonly 파일 mount·model 길이·별칭·port를 기존 소비 계약과 대조한다. 기존 고정 template은 원 단일 user text 대조에 사용하는 형식이며 실제 chat 성공이나 instruction 모델 지원을 미리 주장하지 않는다.

초기 EXAMPLE 계약에서 정확한 기존 예외 타입을 잘못 기대한 오류는 보존하고 기대 타입만 정정했다. 제품 결합 동작·보호 조건·기한을 완화하지 않았다. 실제 입력 미제공·GPU_READY=false, runtime_owner_currently_verified=false, template_file_in_container_read=false, independent_worker_consumption_verified=false를 유지한다. serializer는 source-root 자체의 commit 권한이나 현재 runtime을 인증하지 않으므로 S2가 source/자산 pin과 실제 배포·조회 시각·소유권·모델/template mount·반출 조건을 별도 고정해야 한다. 제품 실행기는 원 template snapshot 검증을 다시 수행한다.

[검토용 전용 결합 코드](implementation-snapshots/sup03_cold_target_binding.py.txt)의 원 bytes SHA256은 `9de45928324bbf91e093cc27530f51835b1a83882a16031149e2dea124e8ff82`다. 준비 freeze 참조는 `worktrees/byul-gpu-b-prep-recovery-errors-20261104/out/sup03-cold-target-binding-v1/freeze.json`, SHA256 `eb2ca8560f68029d32127260149c2f4c26f94143b1aac4b60266fb6afdfa103c`, 게시 담당 정적 검토 참조는 `worktrees/byul-gpu-b-stage1-closeout-handoff-20261106/out/team-handoff/sup03-cold-binding-static-review-v1.json`, SHA256 `757ab9bca7dbe2bd6482a45ebdbd2f2af6bdb4a41aac96ade3d3c81eb55f1298`다. 이 정적 준비 인계 범위에서 새 차단 결함은 발견하지 않았으며 실제 운영 패킷 승인이나 제품 완료를 뜻하지 않는다.

## REQ-08 실제 진단 1회 — registry child·입력 처리·정리 검증 완료

2026-11-06 22:58:21 KST 세션6이 실제 CPU 진단1회의 새53개 원자료와 producer→registry child→consumer 연결, source·원 읽기 전용 입력8개·종료·소유 정리를 검증해 인계했다. 원 caller는40.324초였고 host/container 종료0·OOM=false·정리 오류0이었다. 실제 ModelConfig·renderer·InputProcessor를 사용했고 원 registry 자식1개의 정확 argv·PID/startticks·표준 pipe3개·진행 중 상태와 실제 communicate 종료0을 확인했다. stdout0 bytes·stderr257 bytes의 전체 반환 hash를 보존했으며 진행 중 returncode=null을 최종 종료값으로 쓰지 않았다.

정상 대조6건은 반환됐고 입력 metadata가 유지됐다. 새 타입5건 중 fractional 목록은 renderer/InputProcessor 반환까지 type·length·canonical SHA가 같았다. numeric-string·mixed-int-string·nested-list는 InputProcessor에서, scalar-int는 renderer에서 원 TypeError를 냈다. 이 결과를 타입 안전 PASS·정책상 허용/거부·보안 판정이나 제품 완료로 바꾸지 않는다. TypedDict mapping 생성만으로 입력 유효성이 검증된다는 주장도 하지 않는다.

검증기의 통합 source assertion에 조건식 오류가 있어 후속 검토에서 spec/invocation/result/operation/outcome의 실제5개 source 필드를 명시적으로 다시 대조했다. 모두 원 source `3b3ff137b31b830403ccce1997dabf26319bac4f`와 일치했고 기존 PID/UID 개별 검증도 유지했다. 이 정정은 검증기 문제이며 원 자료·원 판정을 수정하지 않았다.

이 고정 진단의 추가 필수 검증은0이다. 범위는 LAB_DRAFT·CPU_COMPONENT_NOT_FORMAL_PRODUCT_METADATA이며 serving API·최종 모델/worker·샘플러·GPU·제품 factory는 검증하지 않았다. 제품 EVD/RES/RPT와 복원·저장 팩 재판정 대상은 없으며 과거90초 timeout 원인·과거 child 종료값을 소급 확정하지 않는다. 25개 한정 마감 제안/18개 제품 증거 필요 분류는 유지한다.

최종 인계 참조는 `worktrees/byul-gpu-b-next-verify-20261106/out/reviews/req08-registry-delta-20261106-v1/actual-final-handoff-v1.json`, SHA256 `ca16d5dc651292caf963a433e0a8cc07c26ab401edac4a873495c75de79d3c91`이다. 원 검토 SHA256 `38f35cdcab2a941eb71aa203cad35bec4d9d003c4afad1c274290169756bd3a0`와 같은 디렉터리의 `actual-source-correlation-followup-v1.json` SHA256 `2356e51727e13374ee4f5bf88f210c11da6ad31df6ed835233a16007acbfb171`를 함께 따른다. 검토자는 실제 caller/모델/컨테이너 시험을 반복하지 않았다.

## SUP-05 H32 새 입력 — 정상 역할 1회 전 변경 검토 완료

2026-11-06 23:12:54 KST 세션6이 별도 새 H32 입력 패킷의 필수 변경 검토를 완료했다. 새96개 자료의 bytes/SHA, 실제 safetensors23개 tensor 형상·값·A/B 한 좌표 차이, 원 config에서 hidden_size4→32만 바뀐 사실, 실제 생성/소유 정리와 manifest→ENV/model/revision→scope/target/session→native17 준비→고정 argv 연결을 대조했다. 기존 worker/관측기·source6·원 기한·독립 wait·정리 검증은 재사용했다. 원 head4 실패를 수정하거나 새 모델을 같은 실행 세대로 재태깅하지 않았다.

당시 freeze/validate-only 도구 기록은 재실행 없이 원 세션 기록의 정확한 두 항목만 추출해 연결했다. compound 명령의22:52:11.570~22:52:17.415 KST·종료0·실제 stdout과 source import origin을 확인했고, 개별 하위 명령의 시작/종료·exit를 따로 계측하지 않은 한계는 유지했다. class-only/입력 검증을 실제 제품 factory 실행으로 표시하지 않는다.

이 정확 패킷의 차단 결함·필수 추가 입력은0이다. 기존 승인 범위의 CPU normal1회만, 실행 직전 자원·다른 모델0을 확인한 뒤 진행하도록 인계했다. 원90초/85초 진단·독립 wait30초·2CPU/4GiB·UID/GID·원 tmpfs/네트워크/읽기 전용 보호를 유지한다. 중간 자료 대조나 검토 완료만으로 실제 요청/native 성공을 인정하지 않으며 challenge/recovery 자동 확대·자동 retry는0이다. public scope의 Docker 거부와 독립 CPU 준비·제품 연결의 구분도 유지한다.

최종 변경 검토 참조는 `worktrees/byul-gpu-b-next-verify-20261106/out/reviews/sup05-h32-input-delta-20261106-v1/input-delta-review-v1.json`, SHA256 `17f30a84f8eb10680f23003a6928df360e7b9ed4297c5484f63eb3a79ef565aa`다. 사전 검토 당시 새 H32의 실제 요청·native 봉인·제품 Ref는 미관측이며, 실제 결과는 별도 검증한다.

## SUP-03 운영 입력 준비 — 정상 imageID 표기 거부 수정 검토

세션2의 새 배포 후보·읽기 전용 원 API 수집기 cold-v1을 게시 담당이 정적으로 검토했다. 고정 파일7개의 bytes/SHA와 shipped template의 실제 binaryData/SHA를 확인하고, engine/probe·고정 template/모델 mount의 호환성은 세션1의 고정 검토를 재사용했다. 후보의 UID/IP/판본/현재 모델 상태를 실제 관측값으로 채우지 않았으며 운영 전체 패킷은 아직 준비 중이다.

수집기가 정상 Kubernetes imageID를 거부하는 F1을 실행 전에 발견했다. 기존 원GB09 API 기록은 동일한 고정 이미지 digest를 `docker.io/vllm/...@sha256:...`로 반환하지만 v1은 짧은 저장소 표기와 bare digest만 허용했다. 새 v2는 같은 저장소·고정 digest의 정확한 세 표기만 허용하고 원 raw imageID를 변환하지 않는다. 다른 저장소/digest·태그만 있는 값·접미사·비문자열은 거부한다. 원 v1과 smoke/freeze는 보존했다.

게시 담당은 새 함수와 호출1곳 외 AST 동일, 새 collector/시험/CPU 기록 SHA 및 좁은 계약11개 결과를 대조했다. 기존 CPU·archive CRC·실제 Kubernetes/AWS/모델을 재실행하지 않았다. F1은 이 cold 변경 범위에서 해소됐으며 source/정책·기한·소유권 조건을 완화하지 않았다.

수정 freeze 참조는 `worktrees/byul-gpu-b-run-lif14-labels-20261105/out/SUP16-live-normal-v1-operator-20261105/SUP03-fixed-template-operator-prep-v2/freeze.json`, SHA256 `3ae34a0cbd7ddfd133a72de393ff08d630c81f168c8c3d233c4aceae99ba1c40`다. 게시 담당 최종 정적 검토는 `worktrees/byul-gpu-b-stage1-closeout-handoff-20261106/out/team-handoff/sup03-s2-cold-static-review-v2.json`, SHA256 `06b78c3054d43c2155942ddb28f3450ac4103678602a287d524dccc4195e5443`다. 별도 installer/controller·실제 신원/소유권·새 archive 명령/receipt 결합·현재 자원/예산/보호/정리의 전체 운영 검토는 남아 있으며 GPU_READY=false를 유지한다.

## SUP-05 H32 실제 정상 역할 1회 — 요청 성공·증거 채택 실패 검증 완료

2026-11-06 23:30:16 KST 세션6이 새 H32 normal1회의65개 고유 자료를 검증했다. 원 핵심34개 검증을 재사용하고 추가31개를 처음 대조했으며 native688행의 해시 사슬을 반복 검증하지 않았다. 실제 공개 요청은2×32 출력으로 완료됐고 native END=SEALED·worker 종료0·원 primary=null·소유 정리 오류0이었다. 역할과 정리는71.740초로 원90초 안에 끝났다. 원 head4의 CPU attention 미지원 실패와 별도 새 입력/실행이며 과거 결과를 바꾸지 않는다.

그러나 host는 `NATIVE17_READ_RETURN_NOT_ADMITTED`로 종료1이었다. 실제 FD가 A파일28,696 bytes를 읽어 반환한 hash는 선언 기준과 같지만, READ_RETURN [recording0, seq29]의 root_load/model_object가 null이다. 첫 후속 weight root는seq125에 생겼으므로 원 consumer는 이 읽기를 `loader_root_unavailable`·unjoined1·rows=[]로 남겼다. 요청 성공·봉인을 증거 채택이나 유효 정상 전체 성공으로 해석하지 않는다. UNKNOWN·미확인 사유7개와 승인 권한/worker 배정/tensor·decoder/전체 범위 미확인을 보존한다. challenge·recovery·같은 입력 재시도는0회다.

실제 capture의 data_origin은EXAMPLE이다. verification_level·official·policy_status 필드가 없는 원 자료에 값을 채우지 않았다. 준비 ENV Ref는 존재하지만 제품 EVD/RES/RPT·제품 Store Ref는 생성되지 않아 복원·저장 팩 재판정 대상은 없다. 운영 outcome의 null을 저장된 RES 판정으로 읽지 않는다. 원 실행의 핀 검사·S5의 사후 핀 관측·세션6의23:30 KST 별도 관측을 구분하며 동시적인 전체 파일 전후 receipt가 있었다고 주장하지 않는다.

현재 실제 읽기 시점의 loader/model causal root를 제공할 공통 관측 지원이 필요하다. 해당 root를 뒤의 이벤트에서 수작업으로 소급 연결하거나 consumer 조건·판정·assert·팩을 완화하지 않는다. 반환 담당 범위는 기존 공통 센서/관측 소비 계약 소유자이며 개별 담당자/세션은 미확정이다. 반환물이 오기 전에는 이 동일 실험을 반복하지 않는다.

| 재개에 필요한 반환 | 현재 source의 연결 지점 | 보존할 검증 범위 |
| --- | --- | --- |
| 실제 읽기 시점에 같은 loader/model의 causal identity를 제공하는 지원 producer/profile | `update/src/guard/adapters/vllm_weight_iteration_sensor.py`의 활성 parent STEP/root 생성, `vllm_file_read_sensor.py`의 해당 시점 root 기록 | 실제 producer→FILE_READ→원 consumer 결합과 동일 source/모델/세대. 후속 root를 소급 사용하지 않음 |
| 원 consumer가 요구하는 동등한 연결과 새 고정 지원 버전·영향 근거 | `update/src/guard/collectors/native_model_file_reads.py`의 model_candidate 조회와 unjoined 처리 | 기존 rows=[]/UNKNOWN·독립 실패 보존. root 지원만으로 승인·worker/rank·tensor/전체 scope를 true로 바꾸지 않음 |

최종 검토 참조는 `worktrees/byul-gpu-b-next-verify-20261106/out/reviews/sup05-h32-actual-20261106-v1/actual-final-verification-v1.json`, SHA256 `f58abf71730ec1e7aae9bdfe3981787498610ae702a02753a8f873206b864a64`다. 실제 의존성은 같은 디렉터리의 `actual-admission-dependency-v1.json`, SHA256 `e485e85a80760ac9fcfd666aec2d971436a2731b101813c88c8ed755105375a0`다. 이번65개 인계 자료의 필수 검증은 완료했으며 SUP-05 제품 경로 보완과25개 한정 마감 제안/18개 제품 증거 필요 분류는 유지한다. 최종 인계 목록은 같은 디렉터리의 `actual-final-handoff-v1.json`, SHA256 `0687ce88ded92d5bc97fb575f8141692219dfe9a8e751d1cdd7e3de9e83d32b5`다. 별도 사후 핀 자료38개는 원65개와 시각을 구분해 연결했으며 원 시점의 동시 관측으로 소급하지 않았다.

## SUP-03 source 검사 actor 연결 — 전용 driver 정적 검토 완료

제품 검사를 root 운영자와 직접 kubectl로 실행하고 원 source Git 두 호출만 기존 검증된 소유자 helper로 연결하는 전용 driver를 고정했다. 원 driver·helper·공통 src·manifest·권한·venv·kubeconfig는 변경하지 않았다. Git 소유자 확인을 위한 기존 sudo와 채택하지 않은 kubectl sudo-wrapper는 다른 경로다. root 검사 actor가 기업 검사 권한·POL authority·official 승인이라는 뜻은 아니다.

게시 담당은 고정10개 파일의 bytes/SHA·원 helper 함수·새 CPU8개/CLI help 종료0 기록을 대조했다. 전체 module AST에서 helper 로딩/검증 호출·필요 import·CLI 인자2개 외 변경이 없고 제품 수집·Store·정확 Ref·반출/복원·동일 as-of 저장 팩 재판정·예외 보존 본문이 같은 사실을 독립 확인했다. CPU 근거는 owner/Git/verify 효과를 EXAMPLE로 격리했으며 실제 root/sudo 권한·현재 source owner 전환 성공을 입증하지 않는다. 기존 CPU4+보존1·모델·archive·전체 suite는 반복하지 않았다.

새 로더는 기존 helper의 정확한 고정SHA만 허용하고 일반 파일·lstat/open FD identity·지원 OS의 O_NOFOLLOW·읽기 상한·bytes SHA를 확인해 그때 읽은 동일 bytes를 compile/exec한다. 원 source guard를 monkeypatch하거나 path를 다시 읽어 실행하지 않으며 새 해시를 자동 승인하지 않는다. 원 helper는 repo/.git owner·ubuntu UID1000을 확인하고 원 Git argv2와 manifest/verify_req10/import/팩 검사를 유지한다.

[검토용 helper 결합 발췌](implementation-snapshots/sup03_source_owner_helper_binding.py.txt) SHA256 `59df2a8c3494065ff313c954f70dec53b03f3f3eee55eff55b2d06dc75a25f76`는 해당 고정 상수/함수3개의 AST를 원 코드와 대조한 참조다. 독립 실행기나 설치 완료를 뜻하지 않는다. 원 driver 전체 SHA256은 `f88549218e27788ab524d9a8527ed17f5faf9c05f844007d07360d5bad568150`, 원 helper는 `fb12dbe8d82d6bc027b613a477d6e1a432fa4693c46c6f1380cb38cecdf79fc9`다.

준비 freeze 참조는 `worktrees/byul-gpu-b-prep-recovery-errors-20261104/out/sup03-root-actor-product-v1/freeze.json`, SHA256 `9ab359a22a5b4b8bb8711a34e50b6eb2b2a7d2c4cc0d73ec9868073d3fde9b31`, 게시 담당 정적 검토는 `worktrees/byul-gpu-b-stage1-closeout-handoff-20261106/out/team-handoff/sup03-root-actor-static-review-v1.json`, SHA256 `a93afcfe1788fd888ae2019394bb8693e78adf4b7ecc95133d8ca2f01bca60f0`다. 이 정적 변경 범위의 새 차단 결함은 발견하지 않았다. 실제 root 검사/engine·probe UID1000·root700 결과/user700 입력·원 감독/기한·정리·현재 자원/비용·새 archive producer/receipt 결합은 S2 전체 운영 패킷의 별도 검토 대상이며 GPU_READY=false다.

## 2026-11-07 현재 건 정리 마감 — 후속 실행 중단

세션2가 새로 직접 받은 사용자 응답 “현재 건 정리까지만 진행”에 따라 후속 SUP-03 기동·실증과 추가 운영 준비/검토 배정을 중단했다. 이번 응답은 이전 정리 한정 지시를 잘못 재적용한 사건과 다르며, 앞선 후속 진행 승인보다 나중에 수신한 제한이다. S1/S6에도 이 최신 범위를 전달했고 기존 초안·고정 자산·검토·원자료는 보존한다. 기존 계획·정보성 인계를 새 실행 배정으로 해석하지 않는다.

11월7일00:56:42 KST 로컬 관측에서 정본 current_jobs=[]·해당 운영 Python0, SUP-03 전용 private runtime 미배치·최종 운영 freeze/start intent 부재가 확인됐다. GB09의 원2,203,771 bytes 전체 회수와 무결성/의미·운영 정리의 최종 연결f024ce04는 재사용하며 이번 현재 건 정리의 필수 검증 잔여는0이다. SUP-03의 이미 작성된 cold freeze나 정적 검토가 없었다는 뜻은 아니다.

AWS 상태는 새로 조회하지 않았다. 마지막 실제 관측은 **11월6일22:24:57 KST 소유3대 stopped·공인IP0**이며 현재 시각의 새 관측으로 표시하지 않는다. `$53.08166739362498`도 그 시각의 추정으로 실청구 미확정이고 보존 EBS 비용은 계속된다. 이번 마감에서 새 AWS 조회/변경·새 모델/기동·세션3 재개·자동화는 추가하지 않았다.

범위 확인 근거는 `worktrees/byul-gpu-b-run-lif14-labels-20261105/out/SUP16-live-normal-v1-operator-20261105/current-case-cleanup-only-user-reply-20261107-v1/cleanup-only-resolution-v1.json`, SHA256 `7131df6788ab5632e6880ff2853b3155a67fbeb4327bc3912d0998efa70ab6ab`다. root는 이 원문/SHA·최종 연결과 로컬 상태를 대조해 문서로 인계했으며 동일 운영 검증·실측을 반복하지 않았다.

REQ-08/SUP-05의 이번 진단·검증 인계는 마감했고 SUP-05는 공통 관측 지원 반환 대기다. SUP-03은 검토된 준비 자산과 미실행 초안을 보존한 상태로 후속 실행을 중단했다. 과거 원자료5개는 보유자·공유 경로 미확인으로 유지한다. 43ID의 한정 마감 제안25/제품 증거 추가 필요18 및 원 FAIL/UNKNOWN은 바꾸지 않는다. Draft PR과 이 정리 마감은43개 전체 제품 완료나 기업 승인을 뜻하지 않는다. 후속 재개에는 새로운 사용자 지시와 당시의 고정 운영 패킷·필수 검토·현재 자원/예산/보호·정리 조건이 필요하다.

## 2026-11-07 새 사용자 재개 지시 — SUP-03 다음 작업

사용자가 이 조정 채팅에서 현황 확인 후 다음 작업을 진행하도록 새로 직접 요청했다. 이를 00:56 KST의 정리 한정 지시 이후 후속 작업의 재개로 적용한다. 기존 종료 기록·초안·검토·원 FAIL/UNKNOWN은 변경하지 않는다. 이번 재개 기록 시각은 `2026-11-07T19:12:20.120928+09:00`이며 사용자 메시지의 정확한 제출 시각으로 소급하지 않는다.

첫 대상은 이미 준비한 SUP-03 고정 template 분기다. S2가 최신 AWS 소유 자원·활성 명령·공인 IP·보존 볼륨과 비용을 읽기 전용으로 다시 확인하고, controller/installer·원 감독/기한·root 출력/UID1000 입력·실제 ENV/target·새 archive producer CID/receipt 결합을 하나의 고정 운영 패킷으로 완성한다. S6는 기존 binder/collector/root-driver/helper의 정적 검토와 CPU 근거를 재사용하고 새 운영 결합만 검토한다. 패킷·직전 자원/예산/보호/정리 조건이 충족되면 기존 승인 범위의 단일 L4/TP1 실행1건으로 연결한다.

운영 방식은 사용자가 세션2에 지정한 기존 AWS 브라우저 콘솔을 활용한다. 로컬 CLI 인증 만료로 첫 상태 조회가 실패한 뒤 콘솔 연결을 확인했으며, 콘솔/가능한 CloudShell actor의 실제 신원·명령/receipt·작업경로와 파일 업로드/다운로드·새 archive 결합은 새 운영 검토 대상이다. 이전 로컬 CLI/WSL 검증을 새 브라우저 경로의 성공으로 읽지 않는다.

마지막11월6일22:24 KST AWS/비용 값을 현재 관측으로 복사하지 않는다. 새로운 관측은 S2의 시각별 반환으로 반영한다. 전체 예산$100·예비$5·다음 최악 비용과 정리 포함$95 미만, 원 개별 기한·소유권·정리 조건을 유지한다. 준비 중 GPU를 유휴 기동하지 않으며 새 권한·TP2·공통 코드 변경으로 범위를 넓히지 않는다.

기존 CPU/모델/GB09·archive 검증과 H32 동일 실험은 반복하지 않는다. 과거 원자료5개의 보유자/공유 경로 미확인과 공통 관측 지원 반환 대기도 유지한다. 현재43ID의25개 한정 마감 제안/18개 제품 증거 추가 필요 분류는 새 실행/검증 근거 없이 바꾸지 않는다. 별도 사람/root 수신 ACK gate·새 채팅·에이전트·자동화는 추가하지 않는다.

재개 기록의 프로젝트 상대 경로는 `worktrees/byul-gpu-b-stage1-closeout-handoff-20261106/out/team-handoff/user-resume-sup03-20261107-v1.json`, SHA256 `d7dfcb9602abca00bff19d0b1be2f0c371ed35262a5a3bd3383811c0a5e04647`다. 실제 SUP-03 기동·EVD/RES/RPT·복원/재판정·정리 완료를 이 배정만으로 인정하지 않는다.

## 2026-11-07 19:19 KST 브라우저 현재 자원·비용 관측

S2가 사용자가 지정한 브라우저 CloudShell에서 읽기 전용7종 관측을 수행했다. 19:18:47~19:19:46 KST의 호출별 기록과 완료 JSON28,805 bytes를 실제 UI 다운로드해 SHA를 확인했다. 게시 담당도 로컬 사본 bytes/SHA·동일 소유 계정·자원 요약·장부 산술을 대조했다. 소유3대 모두 stopped·공인IP0·각 활성 SSM 명령0·EIP0, gp3 보존 볼륨 합440GiB와 기본 IOPS3000/throughput125가 확인됐다. AWS 변경·신규 기동/SSM 실험/모델 실행은0이다. 호출 시각은 비동시 기록이며 이후 미래 상태를 보증하지 않는다.

11월6일22:24 KST 장부 이후 보존 EBS 추정$1.165898484649788을 더해 새 관측 시각의 누적 추정은 `$54.24756587827477`, 예비$5를 제외한 잔여는 `$40.75243412172523`다. S2가 제시한 다음 건 최악 비용 추정$5.62를 더하면 `$59.86756587827477`로$95 기준 이내다. 실청구 확인값은 아니고 실제 시작 전의 전체 운영 예산 검토도 남아 있으며 보존 EBS 비용은 계속된다.

새 browser actor의 UID/GID·Python/도구/작업경로는 실제 기록으로 구분했다. 이전 로컬 CLI/WSL 신원·실행 성공을 새 CloudShell에 소급하지 않았다. 다운로드한 관측 JSON의 확인은 실제 제품 archive의 새 producer/receipt/전체 반출 검증을 대신하지 않는다. 소형 파일 roundtrip·새 제어/반출과 전체 운영 패킷 검토를 계속한다.

관측 참조는 `worktrees/byul-gpu-b-run-lif14-labels-20261105/out/SUP16-live-normal-v1-operator-20261105/SUP03-fixed-template-operating-packet-v1/observations/browser-current-resources-v1.json`, SHA256 `458ec938955fa6931c892b791d0bb9043fd36eb0e77e5b2405bd1565efa98fc5`다. 게시 담당 요약 대조는 `worktrees/byul-gpu-b-stage1-closeout-handoff-20261106/out/team-handoff/browser-current-resources-summary-20261107-v1.json`, SHA256 `f953aace3608ebb8dc2dbfdebfc0fca846961ec7fe0d2e8e8a173c1a58fec004`다. 계정·인스턴스·볼륨·인증 식별자나 원 AWS JSON은 게시하지 않는다.

## 2026-11-07 브라우저 제어·게스트 준비 코드 검토

세션1은 guest installer/controller·고정 입력을, 세션2는 브라우저 CloudShell 제어·전송을 병행 준비했다. 세션6은 기존 검토·CPU·원 archive를 재사용하고 새 변경과 결합만 검토했다. 호출/검토를 기다리며 GPU를 켜두지 않았고 공통 제품 source·정책·판정·기한·권한은 변경하지 않았다.

브라우저 client의 초기 exporter가 과거 private 경로의 기록을 새 공유 경로와 혼용할 수 있는 F1을 발견했다. client v2는 정확한 현재 private origin·actor·권한·자체 bytes·최종 packet·동일 driver/terminal의 결합을 읽기 전에 확인하도록 수정했고 세션6 검토를 통과했다. 원 정수 실패 exit의 관측·반출을 허용하되 원 실패를 성공으로 바꾸지 않는다. 소형 실제 UI 전송/회수와 읽기 전용 결과 ZIP 검증은 제어 경로 근거이며 제품 archive 성공을 대신하지 않는다.

게스트 결합 검토에서는 system Python의 사전 관측에 사용한 `-I -S -B`와 실제 정리 호출의 `-I -B`가 다른 G1을 발견했다. guest v2는 실제 flags와 원 양성 proof의 정확한 정수값을 기록·대조하고, client v3는 정리3단계와 정확 archive 명령을 `-I -S -B`로 일치시켰다. 제품3단계는 기존 retained Python/옵션을 유지했다. 고정 guest v2/client v3 조합에서 G1이 해소됐으며 새 코드 변경 범위의 구체 차단 결함·필수 검증 잔여는0이다. 이는 전체 운영 준비나 실제 guest 성공 판정은 아니다.

새 설치·workflow CPU18개와 flags 변경 CPU5개 기록을 검토했다. 모의 API·격리된 자체 임시 경로·실제 로컬 Python 시작 옵션의 근거이며 실제 AWS/Kubernetes/모델 관측으로 세지 않는다. flags 양성 시험은 시험 runtime 경계에서 중단했고 `-S` 없는 호출의 거부를 확인했다. 원18개·기존 CPU/모델/GB09·archive를 반복 실행하지 않았다.

| 최종 선정 준비 | 고정 SHA256 | 세션6 최종 검토 SHA256 |
| --- | --- | --- |
| guest v2 freeze | `a35443bb242307819a6106371fcd116b46d545847b9fd1034c4bd2dce346ce49` | `c5a51fc960b35e3847e0661509dad15c21143f54be5f3b324258f15d58f208ef` |
| browser client v3 freeze | `1576803af21f53710d75cafe1ef61e729624dc76785b3fd45ab1123090b4c2eb` | `0ad706aa93dd711c1911f9637c4f42fa00684ced7dbf41ea2dd2adac02a72c9e` |

원 F1·G1·v1/v2 검토는 보존한다. 검토 참조는 `worktrees/byul-gpu-b-next-verify-20261106/out/reviews/sup03-browser-client-20261107-v1/client-review-v2.json`(SHA256 `97f07b4a4aaf9d757ff051dcfd7fb614c311aa1afbd0f3721fd667114d89d18b`), 같은 디렉터리의 `client-review-v3.json`, 그리고 `worktrees/byul-gpu-b-next-verify-20261106/out/reviews/sup03-guest-workflow-20261107-v1/guest-review-v2.json`이다. 게시 담당은 고정 검토 파일의 bytes/SHA와 결과 범위를 대조했으며 동일 시험을 다시 실행하지 않았다.

남은 순서는 최종 패킷·실제 CloudShell 배치/전송·기동 직전 자원·비용·원 감독/정리의 전체 결합 검토, 승인 범위 단일 기동, 모델 배포 전 실제 guest 양성 preflight, 선정 제품 분기1회, 결과 반출·필요 Ref 복원/재판정·정리/정지·비용·실제 결과 검증이다. 꺼진 guest의 proof를 사전에 만들거나 별도 사람/root 수신 ACK를 추가하지 않는다. 준비 검토만으로25개 한정 마감 제안/18개 제품 증거 필요 분류를 바꾸지 않는다.

## 2026-11-07 기동 전 전체 운영 결합 검토 완료

> **후속 정정:** 아래의 기동 전 차단 결함0 결론은 실제 실행 후 발견한 JSON 전송 계약 누락으로 정정한다. 원 검토는 당시 기록으로 보존하며, 아래 실제 startup·후속 오류 기록을 함께 확인한다.

세션6이 고정 source·guest v2/client v3와 실제 CloudShell 배치, UI 전송/다운로드, 새 자원·비용 원자료, 단일 runtime 발행·호출, 원 감독·기한·정리·archive producer 결합을105개 항목으로 확인했다. 기동 전 검토 범위의 구체 결함·추가 필수 입력은0이다. 기존 코드·CPU·source ZIP·GB09 검증은 재사용했다.

새 원자료는 19:50:30 KST 완료의 읽기 전용7종 관측이다. 소유3대 stopped·공인IP0·활성 명령0을 확인했다. 누적 추정은 `$54.2761120275903`, 예비$5 제외 잔여 `$40.7238879724097`, 다음 건 최악$5.62와 정리 포함 합계 `$59.8961120275903`로$95 미만이다. 실청구 미확정·보존 저장소 비용 지속을 유지하며, 호출별 비동시 관측을 한 시각의 원자적 snapshot으로 바꾸지 않는다.

긴 로컬 경로에서 whole ZIP/ASCII 전송 실패를 보존한 뒤 짧은 전용 경로로 새 ZIP89,115 bytes의 UI 업로드·CloudShell 배치에 성공했다. 경로 길이가 단일 원인이었다고 확정하지 않는다. 실제 배치12파일의 bytes/SHA·actor·권한과 새 원자료 ZIP14,061 bytes/18member의 최초 CRC·원 CLI producer→소비값·UI 반출 일치를 검증했다. 준비 전송의 성공은 향후 제품 archive 회수 성공을 대신하지 않는다.

S2는 제안 원본의 `not_ready=false`와 해당 고정 리뷰 참조만 변경해 최초 runtime packet을 배타적으로 발행하고 실제 새 SHA를 원 단일 호출에 사용한다. 최초 start 허용 창은 **20:20:30.231007 KST까지**이며 연장하지 않는다. 창 안에서 시작한 뒤에는 원 개별 실행 기한을 따른다. 단일 기동 후 실제 source/owner·system Python/root/실행 flags·boot/node/GPU/model·원 timer의 양성 preflight가 모델 배포의 선행 조건이다. 실패는 원 오류·가능한 증거·원 정리/단일 stop180과 불확실성 차단으로 연결한다. 별도 사람/root ACK를 추가하지 않는다.

비용 영수증 경로 변경은 게시 담당이 원 코드와 좁게 병행 검토했다. `T`와 `PRIVATE` 상수2개 외 전체 module AST가 같으며 단가·산술·시작 intent부터의 보수 계산·실제 중지 관측 후 close·원 backup/배타 receipt/원자 장부 교체는 그대로다. 실제 원 start/terminal/정확 packet의 동일 bytes 로컬 사본과 중지 receipt를 소비해야 하며, 이미 기록한 prestart를 재실행하지 않는다. 이번 정적 검토에서 updater나 AWS/장부 쓰기는 실행하지 않았다.

전체 운영 리뷰 참조는 `worktrees/byul-gpu-b-next-verify-20261106/out/reviews/sup03-whole-operating-20261107-v1/operating-join-review-v1.json`, SHA256 `3cf1f0757b62e824f80e69ca393283e0a7cc5903cfe481a6be7ddafa22d7475e`다. 비용 경로 리뷰는 `worktrees/byul-gpu-b-stage1-closeout-handoff-20261106/out/team-handoff/sup03-browser-cost-bookend-static-review-v1.json`, SHA256 `25d8182cd56019c7bae5ff8c42570362df060108cd2d10e60ebb007b68326d65`다. 이 기록만으로 실제 start/SSM/worker·EVD/RES/RPT·최종 정리 성공을 선언하지 않으며43ID 분류는 유지한다.

## 2026-11-07 실제 startup 검증과 준비 전송 결함 후속 정정

세션6이 startup 원ZIP10,130 bytes의7member 최초 CRC/bytes/SHA와 실제 UI 다운로드·로컬 사본 일치, runtime packet의 원 제안 대비 허용된 두 필드 변경, 단일 기동 수락·시작 감독 종료0·실제 execute 부모/자식·health 명령의 신원을 확인했다. 최종 packet SHA256은 `3819025ba775ad651e9ff64afd12aa88386f31a505e5644db33f4df5594c54b6`다. 원 start intent는20:15:13 KST, 시작 감독 종료는20:16:09, execute 제출은20:20:05이며 최초 기동 창 안의 start와 후속 execute를 구분한다.

그러나 고정 driver가 `operator-payload.json`을 helper의 put에 전달하는 반면, 해당 고정 helper는 `.py`·`.zip`·`.tar.gz`·`.whl`만 허용한다. JSON 이름은 remote init 이전의 파일명 검사에서 거부되므로 installer·guest preflight·prepare/observe로 연결될 수 없는 구체 결함이다. 최초 전체 결합 검토에서 이 caller→전송기 계약을 연결해 확인하지 못했다. 원 리뷰와 실제 실패를 삭제하거나 소급 성공으로 바꾸지 않고 후속 정정으로 연결한다. 이미 검토한 F1/G1 수정의 범위와 이번 누락은 구분한다.

S2는 원 execute 종료1과 같은 전송 오류를 보고했고 원 감독의 단일 정상 stop 요청을 확인 중이다. 이 startup 검토 시점에는 실행 stderr와 최종 control·실제 중지 원자료가 아직 별도 검증 중이므로 제품/전체 정리 성공을 선언하지 않는다. health 제출·기동 수락·장비 상태 확인을 제품 EVD/RES/RPT/Store·모델 실측 완료로 올리지 않는다. 새 기동·같은 실행 반복·원 기한 연장은 하지 않는다.

비용 open은 게시 담당이 원 intent·정확 packet·원 before/after 장부와 receipt의 SHA 및 산술을 대조했다. 19:50:30부터20:15:13 KST까지 저장소 추정$0.02297373488084889가 더해져 intent 시점 추정은 `$54.29908576247115`이며 그때부터 compute/IP를 보수적으로 열었다. 실제 중지 관측 전에는 이 값을 최종 비용으로 읽지 않는다. 실청구 미확정·EBS 지속·최종 close 검증 대기를 유지한다.

실제 startup 검토 참조는 `worktrees/byul-gpu-b-next-verify-20261106/out/reviews/sup03-browser-actual-20261107-v1/startup-actual-review-v1.json`, SHA256 `48d48c0d0f4e4690bb6aed58ae5e6995cf0ac9052e69ace654e323d4052b2c52`다. 같은 디렉터리의 `payload-transfer-contract-review-omission-v1.json`, SHA256 `2b83e44518685bffbfd27fe86c1fb1cdf85c455da04a305b5d508d52142d6e91`은 사전 검토 누락과 후속 수정 담당·입력 계약을 기록한다. 비용 open 검토는 `worktrees/byul-gpu-b-stage1-closeout-handoff-20261106/out/team-handoff/sup03-browser-cost-open-actual-review-v1.json`, SHA256 `dfc225eccf6318814acf6023e39ba2007532815ab8051f5ac5fdc6c992ee9ec6`다. 원ZIP/코드/CPU/GB09는 반복 검증하지 않았다. 25개 한정 마감 제안/18개 제품 증거 필요 분류는 유지한다.

## 2026-11-07 비용 close 산술 검증 — 원 정지 실패와 후속 중지 구분

게시 담당이 새 before/after 장부·cost-close receipt·정확한 후속 EC2-only JSON의 SHA 연결과 산술을 확인했다. 원 start intent20:15:13.789979부터 후속 중지 관측20:30:45.417836 KST까지931.627857초를 보수적으로 계산했다. 해당 기간 compute$0.25609414646866663·IP$0.0012939275791666665·EBS$0.014426774936676667을 더한 추정은 `$54.57090061145566`, 예비$5 제외 잔여는 `$40.42909938854434`다. compute/IP 구간은 닫혔고 보존 EBS 비용은 계속된다. 실청구값은 아니다.

후속 EC2-only 사본에는 소유3대 stopped·공인IP0이 기록됐지만 정확 off 전환 시각은 미확인이다. 이전 full7의 stopping/IP1과 원stop180 감시 실패를 후속 중지로 덮지 않는다. 전체 CLI/control/UI 원자료 진위와 원 실행 범위는 세션6의 후속 검증 대상이며 게시 담당은 같은 ZIP CRC를 반복하지 않았다.

원 API wrapper의 `at`는 실제 CLI 수락 시각과 다르다. 비용 시작은 원 intent의 보수 anchor를 유지하며, start 실제 CLI는20:16:07~20:16:09 KST, stop 실제 CLI는20:23:38~20:23:39 KST다. 원 wrapper 시각을 실제 API 완료 시각으로 소급하지 않는다. 정확한 mutation intent/result·stdout/receipt 시각은 세션6 원control 검증에 연결한다.

비용 close 검토 참조는 `worktrees/byul-gpu-b-stage1-closeout-handoff-20261106/out/team-handoff/sup03-browser-cost-close-actual-review-v1.json`, SHA256 `fefe069ba6bdc5ee4f29b2a4e356b224c3375a7c6a5667cf4c1a6eff1ab73ff4`다. 원 장부·영수증·단가·정책을 수정하지 않았고 제품 성공·재실행·실청구 확인을 뜻하지 않는다.

## 2026-11-07 SUP-03 R1 실제 실패·정지·비용 구간 최종 인계

세션6의 R1 최종 필수 검증이 완료됐으며 이번 운영 마감 범위의 추가 필수 입력은0이다. 실제 control ZIP485,650 bytes의 최초422 outer CRC·421 payload bytes/SHA·UI 원본/사본 일치, 실제 runtime/source12파일·기존 startup7 재사용, adapter의 원 request→stdout/stderr SHA→receipt→소비값과 실행 신원을 확인했다. 기존 CPU·GB09·nested archive·과거 Ref 검증은 반복하지 않았다.

원 start·health 제출·정상 stop은 각각1회 수락됐고 불확실성 기록·자동 재시도는0이다. health는 종료0으로 물리 L4·원 timer·유지된 bootstrap source `3bba3c73a6bee1966b784cd90a9690e3e9d2512a` 범위를 확인했다. 선택 제품 source `3b3ff137b31b830403ccce1997dabf26319bac4f`의 guest preflight와는 다른 범위다. payload helper는 JSON 확장자 오류로 종료1이었고 installer·제품 source 사전 검증·모델·namespace·factory에 도달하지 않았다. 새 제품 EVD/RES/RPT·Store Ref가 없어 복원·저장 팩 재판정 대상도 없다. 해당 미도달을 제품 PASS/공식 GPU MEASURED로 채우지 않는다.

원 execute는 종료1·child 회수/group 종료·cleanup 오류 목록[]였지만 원 정상 stop180 감시는 실패했고 감독 final 파일은 없다. 이후20:29 KST full7은 대상 stopping/IP1·SSM0/EIP0·EBS440GiB를 기록했다. 그다음 **20:30:44.662938~20:30:45.417661 KST EC2-only 원조회**와20:30:45.417836 집계에서 소유3대 stopped·공인IP0을 확인했다. 집계만 사용하지 않고 새 반출 ZIP2,728 bytes의 원 stdout/stderr2개를 최초 CRC/bytes/SHA·UI 원본과 대조해 producer→집계 값의 일치를 검증했다. 정확 off 시각은 미확인이고 원stop180 실패를 후속 stopped로 소급 성공 처리하지 않는다.

비용은 게시 담당의 open/close 검토를 재사용해20:30:45 KST 추정 `$54.57090061145566`, 예비$5 제외 잔여 `$40.42909938854434`, compute/IP 구간 닫힘과 EBS 비용 지속을 연결했다. 실청구 확인값은 아니다. 원 API wrapper intent와 실제 CLI 수락 시각도 구분해 정정했다. 실행·정지 관측과 비용 계산을 한 건의 의미로 합치지 않는다.

| 필수 검증 | 프로젝트 상대 참조 | SHA256 |
| --- | --- | --- |
| 실제 control 최초 무결성 | `worktrees/byul-gpu-b-next-verify-20261106/out/reviews/sup03-browser-actual-20261107-v1/control-integrity-review-v1.json` | `44bc5ede27c22fdd88537c573e5613775b58ab4b8d2cb6129ab475bdc807e90a` |
| 원 실행·실패·원 정지 범위 | 같은 디렉터리 `control-runtime-review-v1.json` | `1f9c7af739549a0e3125f472d71da945b9b3cdfa9409d29d2a6d010a9d7603dd` |
| 후속 실제 stopped와 R1 최종 인계 | 같은 디렉터리 `r1-final-actual-handoff-v1.json` | `8d5fa2d7717f30e804e9904e9c33948c1d22496e8dbf42be9b5adf0384fdf2b2` |

게시 담당은 위 고정 리뷰의 bytes/SHA·최종 범위와 비용 영수증 산술을 대조했고 같은 원 archive를 다시 검증하지 않았다. 사전 검토 누락2b83과 원리뷰3cf1/0ad706·원 실패/원자료를 보존한다. 이번 마감은 실패한 선정 실행의 결과·정리·후속 정지·비용 구간 검증 완료다. 25개 한정 마감 제안/18개 제품 증거 추가 필요와 전체 분기 완료 미확정은 유지한다.

다음 준비는 S1의 실제 단일member ZIP→기존 JSON 검증 installer 계약과 S2의 동일 ZIP producer/put/호출 최소 변경으로 나눴다. 원 helper 허용 제한·제품 source/venv/모델·판정·기한을 바꾸지 않는다. 새 고정 delta 검토와 당시 자원·예산·보호 조건이 준비되기 전에는 새 GPU/SSM을 실행하지 않으며 원 R1의 기한·실패를 재설정하지 않는다. 이번 배정은 새 실행 성공이나 ZIP 변경 검토 완료를 뜻하지 않는다.

## 2026-11-07 SUP-03 R2 ZIP 입력·호출 변경 검토 완료

세션6이 새 R2 source/CPU 변경을105개 항목으로 검토했다. 실제 ZIP47,079 bytes에는 원 `operator-payload.json`137,721 bytes가 정확히 한 member로 들어 있으며 원 JSON SHA256 `6dd28b792eb5f7c8bae12687d0c0a4ca5279ac31b7da81e5445a1a12fe796f7a`를 유지한다. 새 입력 경계는 ZIP을 제한해 읽고 기존 JSON 검증/decoder로 넘긴다. 기존 installer의 JSON/설치 본문과 helper 확장자 제한·제품 source/venv/모델·기한은 그대로다.

고정 실제 ZIP producer→새 ingress→원 decoder11자산 연결, helper가 허용하는 실제 파일명, caller의 명시 ZIP/ZIP SHA·installer source SHA·stdin0 계약을 확인했다. 새 입력/오류 처리 CPU16개 통과 기록은 로컬 WSL의 자체 임시 경로 경계 시험이며 원격 installer·worker·모델 성공을 뜻하지 않는다. 원 selected venv의 `-I -B`/inline 실행 계약을 유지하고 최초 전달의 system Python 명령 예시는 후속 정정했다. cleanup/preflight의 기존 별도 system Python/no-site 계약과 혼용하지 않는다.

이 고정 변경 범위의 구체 결함·필수 추가 입력은0이며 원 JSON 전송 거부 원인은 로컬·정적/CPU 범위에서 해소됐다. 기존 guest/client·R1 최종 결과·CPU/모델/GB09/Ref·전체 suite는 반복하지 않았다. 새 candidate는 NOT_READY를 유지한다. 다음 필수 작업은 새 R2 actual source13파일 배치·actor/권한/bytes·UI 근거와 새 full7/원 예산·최초1800초 창·proposal/invoker의 전체 운영 결합이다. 새 원격 전송/installer·제품 성공을 이 코드 검토로 인정하지 않는다.

검토 참조는 `worktrees/byul-gpu-b-next-verify-20261106/out/reviews/sup03-payload-zip-delta-20261107-v1/payload-zip-source-delta-review-v1.json`, SHA256 `9589a039e4b0c77bf987062a1a83560fa6989c55a94c1e87fd73dc2ff1912396`다. 새 source freeze SHA256은 `35dccb3c0483b91c1429fea7f5a18e70b8d3fbca4b3ecfd871bdd617cf6eb320`, source ZIP은 `0311a27b03179d61c371787f1659ad9c9da5710fdce0ab29c7ee82ce308d3799`다. S1 installer SHA256은 `2aae2679df0f8cefb0c8a45c6899616559519a198ff03a870a5a9c3a90d8bf73`이며 CPU16과 실제 producer→consumer의 범위를 구분했다. 게시 담당은 고정 검토 bytes/SHA·결론을 대조했으며 같은 시험·archive를 다시 실행하지 않았다. R1 원실패와 최종 인계8d5fa2,43ID 분류는 유지한다.

새 R2 prestart 비용 산술은 게시 담당이 원R1 after-close와 정확히 같은 before 장부에서 검증했다. 20:44:40.863450 KST까지835.445614초의 EBS 추정$0.012937339469242224만 더해 `$54.5838379509249`, 예비$5 제외 잔여 `$40.4161620490751`, 다음 최악/정리$5.62 포함 `$60.2038379509249`로$95 미만이다. compute/IP 구간은 아직 닫힌 상태이며 EBS는 계속된다. 새 최초 기동 허용 창은21:14:40.863450 KST까지1800초다. 비용 검토 참조는 `worktrees/byul-gpu-b-stage1-closeout-handoff-20261106/out/team-handoff/sup03-r2-prestart-cost-review-v1.json`, SHA256 `8cdaf9de1e74d40fabc4089ef6ad41f2b646429e664610df94e4f9ca4e54f1ca`다. 새 실제 배치/원조회 producer·최종 패킷의 운영 결합은 세션6 별도 검토이며 이 산술은 새 기동/제품 성공을 뜻하지 않는다.

## 반환 결과의 추가 원칙

다음 반환은 고정 source/version·정확 산출물·필요 검토·실제 실행 환경과 한계를 함께 기록한다. CPU 계약이나 로컬 성능 관측을 실제 AWS 개선·GPU 제품 성공으로 올리지 않는다. 미검토 변경이나 실행되지 않은 계획은 별도로 유지한다. 기존 판정·기한·FAIL/UNKNOWN·원 source와 Ref를 변경하지 않는다.
