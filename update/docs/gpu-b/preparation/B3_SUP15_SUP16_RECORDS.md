# B3 SUP-15·16 record 보존과 pair 연결

기준: `aa3c6ac8f9b7e4a70f8ea76f6f35b89a3b40737c`. 이 문서와 같은 커밋의 소스가 검토 대상이다. 통합·manifest·push는 루트 담당이다. AWS 변경은 없다.

## 변경과 검증 범위

| 점검/분기 | 이번 변경·새 관측 | 실제 검증 | 남은 조건 |
|---|---|---|---|
| SUP-15 입력 | relevant_change/reused/compatibility_verified의 문자열을 boolean으로 해석하지 않고 거부 | 원 실제 CPU record에서 변형한 잘못된 입력 3건 | 현재 배포의 실제 prepared/target·유효 정책 결합 |
| SUP-15·16 저장 | 원 record bytes와 보고된 오류·복구 행을 별도 artifact 및 session에 보존. mkdir/write/collision 실패가 관측을 지우지 않음 | 직접 6건 + 역할 I/O replay 10건. 새 factory/run→EVD→RES→RPT→별도 DB 복원→저장팩 재판정 | 실제 Kubernetes role admission/native ownership는 replay하지 않음 |
| SUP-16 normal writer | 고정 2485 helper의 전체 filesystem API 경로 | source mismatch 거부/실제 write/O_EXCL 재기록 거부 3호출, FD 4→4 | 과거 GB09 journal replay이며 현재 worker/native owner 검증 아님 |
| SUP-16 pair writer | legacy pair의 native_recording 경로가 잘못된 LF reader를 호출하는 잔여 수정. 고정 2485 helper를 명시 입력·SHA 검사로 재사용 | legacy 정상 role에서 chain 오류 재현; 새 normal/challenge 실제 writer 2호출, 동일 producer SHA, FD 4→4. 생성 record→원 validator→등록 factory projection 연결 | 원 CPU journal은 loader root가 없어 ordering=false 유지. worker consumption/record authentication/전체 완료=false |

SUP-15의 기존 **150초** 종료 계약 및 기존 90/45초 세부 계약은 변경하지 않았다. 기존 모델 5회·cold CPU 1회와 원 warm45 실패를 재사용하며 모델 시험을 반복하지 않았다. SUP-16의 기존 safetensors 정상 196/부분 파일 SafetensorError 0/복구 196 결과도 재사용한다. 기존 FAIL·UNKNOWN·원 ENV/Ref를 현재 소스로 소급하지 않는다.

## 근거 경로

이하 경로는 이 worktree 루트 기준이며, 원자료는 이동·복사하지 않았다. CI에는 작은 exact record/ENV metadata fixture만 포함한다. pytest는 비공개 archive·Docker·다른 worktree에 의존하지 않는다.

- `update/out/b3-baseline-repro-v1/`: 원 기준선의 7개 실패 및 invocation.
- `update/out/b3-records-v1/`: 직접 9건 통과. replay 10건의 최초 실패는 시험의 `asof` 인자 오타이며 제품 결함으로 세지 않는다.
- `update/out/b3-records-fixed-test-v2/`: 오타 수정 후 해당 10건만 통과. invocation의 external basetemp 하위 `cpu-contract-refs.json`이 정확 PLAN/EVD/RES/RPT Ref다.
- `out/b3-sup16-writer-cpu-v1/result.json`, `consumer-proof.json`: normal writer 실제 API·소유 자원 삭제·소비 연결.
- `out/b3-sup16-pair-native-v2/result.json`, `consumer-proof.json`: 두 role 실제 API·legacy stderr·각 소유 container 부재·원 bytes 소비 연결.
- `out/b3-sup16-pair-native-v2/{normal,challenge}/fixed-source.json`: 원 raw journal SHA/길이 및 복원 근거 결합.
- 기존 준비 worktree `byul-gpu-b-prep-recovery-errors-20261104/out/sup15-exit-integration-v3/`, `sup16-native17-ordering-writer-delta-v1/`, `sup16-pair-producer-v1/`, `sup16-partial-v1/`의 변경 없는 원 근거를 재사용한다.

writer 실제 API는 기존 cached CPU image에서 UID1000, network none, 1 CPU/512MiB/PIDs32, RO 모델·자산, 소유 tmpfs, 호출 60초 상한으로 수행했다. 새 model/worker/native/GPU/AWS 실행은 0이다. 원 journal을 읽었으므로 EXAMPLE/historical replay이며 native ownership를 주장하지 않는다. 두 소유 container의 정확 ID·label 확인 및 삭제/부재 근거가 result에 있다.

## 실행 담당 인계

새 도구는 `python -I -B tools/gpu_b_sup16_pair_writer.py --help`로 입력 계약을 확인할 수 있다. 기존 `prepare_pair.py`의 source/role/UID/7개 모델 파일·partial receipt·private directory·O_EXCL gate는 유지한다. 기존 normal/challenge writer argv에서 source를 이 커밋의 `tools/gpu_b_sup16_pair_writer.py`로 고정하고 `--ordering-helper <고정 helper 경로>`를 추가한다. 모든 기존 binding/receipt/source SHA 인자는 실제 자산 bytes에 결합한다.

외부 helper는 기존 `sup16-native17-ordering-writer-delta-v1/write_normal_boot.py`, SHA `2485ba44ce8398f833477b79a04c86624cc254316b566f399a4bc9b5f085e4bd`이다. 새 pair source SHA는 `ed9616091017c486cba9bb8e1b804969e6f1944c5fdbbd4b6c7408fa009bb519`이며 양 role 모두 같은 bytes를 사용한다. 기존 stage SHA `c61b7059419659419aa973c072095f5a0ca744c3a24072472ce63074ad9d6f94`는 변경하지 않는다. helper/원 journal/native17 자산을 다른 버전과 섞지 않는다.

런타임 ENV/Pod UID/PID/boot/GPU 신원·target/prepared·정책 유효창은 실행 담당이 실제 실행 시 결합한다. 미래 값이나 기업 승인값을 생성하지 않는다. 기존 150초 및 각 절차의 종료·복구/정리 명령을 재사용한다. 현재 산출물은 **구현·CPU 검증 및 historical producer/consumer 연결 근거**이며, 실제 Kubernetes 양 role·native owner·완전한 GPU 실행 패킷 준비 완료를 뜻하지 않는다. 원 native producer가 없는 분기는 대체 record로 채우지 않는다.

## SUP-16 정적 실측 준비 도구

`tools/gpu_b_sup16_prepare.py`는 기존 도구·명령을 고정하는 준비 도구이며 실행기나 운영 controller가 아니다. GPU/모델/writer/SSH/SSM/AWS를 실행하지 않는다. `prepare_pair.py`를 복제 구현하지 않고, 원 source-owner 검사·정본 manifest 검사·새 도구와 공개 자산의 tracked/clean 검사를 수행한 뒤 다음 입력 bytes를 검증·보존한다.

- 현재 공개 pair `ed961609…`와 공개 normal-helper snapshot `2485ba44…`, 실제 native17 sensor `e32c0a53…`의 호환.
- 명시 외부 입력인 원 `prepare_pair.py`(`c1aff210…`), stage(`c61b7059…`), source-owner helper(`fb12dbe8…`), 원 normal 절차 `commands.json`(`e48fefc2…`). 이 값은 기존 자산 pin이며 새 source 승인값이 아니다. 없거나 bytes가 다르면 새 출력 생성 전에 거부한다.
- 제공된 normal/challenge 사본의 stage receipt·7개 파일 실제 SHA/길이·동일한 전체 reference manifest·model inventory. 모델은 읽기·해시만 하며 복사/수정/적재하지 않는다. 모델 경로를 생략하면 미제공으로 기록한다. 두 역할 또는 reference가 섞인 입력은 거부한다.

현재 source checkout과 같은 고정 commit을 사용하며 새 출력은 저장소·입력·기존 증거 밖이어야 한다.

```text
python -I -B update/tools/gpu_b_sup16_prepare.py --source-root <고정-checkout> --source-commit <40자리-commit> --manifest-sha256 <정본-파일SHA> --stage <원-sup16_stage_artifact.py> --prepare-pair <원-prepare_pair.py> --source-owner <원-source_owner_verify.py> --procedure <원-sup16-live-normal-v2/commands.json> --normal-model <기존-normal-사본> --challenge-model <기존-challenge-사본> --output <새-외부-준비경로>
```

출력 `assets/`는 위 검토된 코드·절차의 exact bytes다. `preparation.json`은 source/manifest·각 자산 SHA/길이·검증된 모델 metadata·누락 조건과 `commands.json` SHA를 담는다. `commands.json`의 기존 pair 준비 argv 및 역할별 writer argv는 **실행하지 않은 계획**이다. `ASSETS`·`ACTUAL_SOURCE`·`<actual-…>`는 운영자가 실제 경로/신원에 결합해야 할 자리표시자다. 원 절차의 옛 source/writer argv는 실행하지 않고 시간·소유 정리 근거만 참조한다. 양 역할은 같은 새 pair source를 사용하고 각각 실제 native journal 경로와 binding/receipt를 사용한다.

운영 순서는 원 정상 대조→부분 challenge→복구 정상이며 세대와 role session을 분리한다. 실제 `prepare_pair.py`가 ENV/image/target·scope·factory/policy를 검증하고 native 자산을 만든 뒤, 기존 installer·`load_prepared`·collector가 실제 소유권을 재검증한다. 이 정적 도구는 그 경로를 실행하거나 새로운 ENV/target/prepared/session을 만들지 않는다. 따라서 자산·모델 두 사본 검증 성공도 `ready_for_execution=false`이며 실제 runtime/정책/Ready/복구 증거는 별도로 남는다. 후속 제품 객체가 실제 생성됐을 때만 원 EVD→RES→RPT·반출·별도 DB 복원·저장팩 재판정을 수행한다.

SUP-16 원 절차의 **asset wait 90초 / collection 120초 / Pod 600초**와 더 엄격한 운영 종료·소유 정리 계약을 유지한다. 위 SUP-15의 **150초**를 SUP-16 기한으로 사용하지 않는다. 종료·출력 수집 실패 시에도 원 오류와 후속 오류를 보존하는 기존 운영 절차를 사용하며 기존 자원·보호 타이머를 임의로 변경하지 않는다. 새 writer LF/record 저장 수정으로 native 인증·worker 최종 소비·post-exit/복구 지원이 생긴 것으로 집계하지 않는다.
