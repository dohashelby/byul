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
