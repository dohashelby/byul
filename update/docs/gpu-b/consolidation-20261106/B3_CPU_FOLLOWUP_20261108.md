# B3 공급물 CPU 후속 검증 — 2026-11-08

코드·입력의 실제 변경과 새 확인 목적이 있는 SUP-05·07·12·13·15·16 분기를 기존 세션으로 진행했다. GPU/AWS/SSM 추가 기동·새 모델/worker 실행은 0이다. 이전 모델·API 시험과 원 실패를 재사용하며, 실제 API 호출·과거 기록 재생·제품 경로 계약 시험을 구분한다.

## 실제 CPU API 관측

| 대상 | 새 입력·관측 | 실제 결과 | 남은 범위 |
| --- | --- | --- | --- |
| SUP-05 | 소유 2×2 numeric safetensors의 C `safe_open/get_slice` | 실제 mmap inode·값·입력 불변·FD/mmap 수명 5개 assertion 통과, 2.000초 | 승인/pre-read 인과·worker/rank·최종 tensor·교체 집행 미확인 |
| SUP-07·12 | 원0.31 전체 weight_utils의 실제 npcache 생성·재사용·잘린 header 거부·복구 | 4사례·7개 assertion 통과. 최초 생성의 torch.load=1, 후속 세 사례=0, 각 np.load 도달 확인 | 비허용 writer·승인/검증 전 집행·전체 reader 부수 동작/fallback 미확인. legacy row를 임의 작성하지 않음 |
| SUP-13 | 실제 HF/Jinja의 정상 literal·구문 오류·후속 정상, 기존 B 정책 진입 | 원 TemplateSyntaxError 후 정상 복구 및 명시 정책 수락·누락/미지원 거부, 5개 assertion 통과 | 실제 endpoint/worker·공급 template 귀속·전체 sandbox 집행 미확인 |
| SUP-16 단일 기록기 | 고정2485 writer의 stdlib·FD 경로 | 잘못된 source 거부→정상 write→O_EXCL 재쓰기 거부 3호출, FD4→4·기존 bytes/원 journal SHA 보존 | 과거 GB09 journal 재생. 현재 worker/native 소유권·GPU 관측 아님 |
| SUP-16 쌍 기록기 | 실제 normal/challenge filesystem 입력과 각 CPU journal | legacy의 잘못된 LF chain 실패 재현 뒤 새 고정 pair 두 role 성공·동일 producer SHA·FD4→4·소유 container 부재 | historical 재생. loader root 공백에 따른 ordering=false, producer 인증·worker 소비·전체 완료=false 유지 |

npCache/HF의 성공 실행은 수집기64.753380초·부모75.453초였다. source0.31 weight_utils SHA `7d257393…`와 설치 패키지 vllm0.29 metadata를 구분했다. Torch2.13+cpu·numpy2.3.5·transformers5.17·Jinja3.1.6·tokenizers0.23.2의 실제 import origin을 기록했다. native extension/CPU backend warning과 실행 전 audit로 차단된 Popen 시도2회도 보존한다. 전체 OS 부수 동작이 없다고 일반화하지 않는다.

이전에 mounted 환경의 npcache import는 부모180초 내 종료 결과를 확보하지 못했고, 별도 HF 준비는120초에 종료됐다. 후자는 배포 metadata의 RECORD/stat 준비 위치 stack을 남겼다. 기존 native LinuxFS CPU 환경을 선택한 실제 준비 경로 변경으로 미도달 API만 실행했다. 같은 실패 호출이나 이미 성공한 C mapping·이전 모델2요청은 반복하지 않았다. v1 종료신원의 사후 미확정과 v2/v3의 관측 시각별 PID/start/group·tmp 부재를 구분한다. 이 CPU 환경의 성공을 원 GPU90초 실패의 원인 해결로 쓰지 않는다.

## 원 기록 소비·저장 경로 수정

SUP-15의 boolean 세 필드와 path SHA를 닫힌 타입으로 검사한다. 문자열 `"false"` 등을 실제 boolean처럼 해석하지 않는다. SUP-15·16의 원 record bytes와 보고된 오류·복구 정보를 기존 선언 session 및 별도 artifact에 보존하고, 디렉터리/쓰기/충돌 오류가 이미 수집한 사실과 독립 FAIL을 지우지 않도록 한다. 다른 점검의 기존 경계·판정 규칙은 바꾸지 않는다.

원 기준에서 입력/저장 오류7개를 재현하고 수정 후 직접9개, 등록 factory/run→EVD→원 judge→RES→RPT→별도 DB 복원·저장 팩 재판정10개를 확인했다. 역할 I/O는 원 CPU record를 제공하는 **EXAMPLE 재생 계약 시험**이다. 실제 Kubernetes 수집·현재 native owner·전체 제품 실측 Ref로 세지 않는다. 첫 재생 시험의 `asof/as_of` 호출 오타10실패는 원 로그에 보존하고 시험 호출만 수정했다. 기존 관련 회귀13개도 통과했다(8.55초).

정상 writer와 달리 legacy pair가 옛 LF reader를 부르던 연결은 `tools/gpu_b_sup16_pair_writer.py`에서 고정2485 helper를 명시 입력·SHA 검사로 재사용하도록 보완했다. source/role/UID/7개 모델 파일·staging receipt·private directory·O_EXCL 검사는 유지한다. 실제 CPU 실행은 cached image·UID1000·network none·1CPU/512MiB/PIDs32·읽기 전용 모델/자산·호출60초에서 수행했고, 정확한 소유 container 제거/부재를 확인했다.

고정 helper는 이미 PR의 [기존 source snapshot](implementation-snapshots/sup16-write_normal_boot.py.txt)에 같은 bytes(SHA `2485ba44ce8398f833477b79a04c86624cc254316b566f399a4bc9b5f085e4bd`)로 보존돼 있다. 실행 담당은 그 bytes를 자신의 준비 경로에서 `.py`로 고정하고 `--ordering-helper`로 지정한다. `.txt` snapshot 자체를 import하거나 다른 판본과 섞지 않는다. 현재 pair source는 `ed961609…`이며 양 role에 같은 bytes를 사용한다. 미래 ENV/target/prepared·정책·worker/GPU 신원을 만들지 않는다.

## 근거와 실제 연동 인계

- [S1 소비·기록기 상세](../preparation/B3_SUP15_SUP16_RECORDS.md): 원 CPU context·record·오류·Ref, 단일/쌍 writer와 helper 인계.
- [S5 API·준비 실패/복구 상세](../../b-work/B3_S5_CPU_20261108.md): v1~v3 실제 API·판본·원 오류·정리 한계.
- [B3 고정 근거](b3-validation-20261108.json): 코드·실행별 원 자료·독립 검토·manifest delta의 정확한 SHA.
- [CI 수정](CI_FOLLOWUP_20261108.md): d1d91329와 B3 통합 c1ae7bcd는 각각 두 이벤트14/14 성공했다. 아래 저장 오류 후속 head의 CI는 별도로 확인한다.
- [B5 검토 의견](../B5_REVIEW_OPINION_20261108.md): impact·확정 정책을 검토했으며 상태를 임의 변경하지 않았다.

남은 실제 연결은 승인 inventory·pre-read/소비 인과, cache writer/검증 전 집행, reader 부수 동작/정책·실제 endpoint, 정상/실패 양 role의 지원 target/prepared·native owner·소유권/유효 정책이다. 이 반환물 없이 CPU 기록을 K8s target으로 바꾸거나 미관측 행을 채우지 않는다. 기존150초 및90/45초 종료 계약, 원 FAIL/UNKNOWN·출처·Ref는 유지한다.

기존 lab DONE2/잔여41·한정25/제품 증거18·이전 실제 로컬14ID는 당시 기준으로 유지하고 이번 B3 추가 근거를 별도로 연결한다. API assertion·재생 계약·완료 ID를 서로 합산하지 않는다. EXAMPLE·historical replay·CPU 관측을 MEASURED나 official로 올리지 않는다.

## 독립 작업 점검에서 발견한 저장 오류 후속

c1ae7bcd 이후 남은 분기를 점검하며 SUP15/16 외의 boot summary 저장 오류가 Draft 반환 전에 전파되는 결함을 확인했다. 원 factory/run→EVD/judge 대표3개에서 mkdir/write 오류의 FAIL→UNKNOWN 손실과 collision의 시험 파일 덮어쓰기·COMPLETED를 재현했다. 기존 persist를 전체13 boot 프로파일의 저장 경계에서 재사용해 원 violations·normal·source/role·진단/cleanup을 보존하고 저장 gap은 ABORTED·complete=false로 남긴다. SUP15/16의 raw record/session 조건과 helper 실행 로직·수집·투영·정책·팩은 유지한다.

새 직접21개, 기존 진단/cleanup 추가1개, 다른 공통 profile 대표6개의 고유28개가 통과했다. 앞선 통과 사례·모델·archive는 반복하지 않았다. 원 실패와 명령·수집/부모 시간·JUnit은 [S5 기존 인계 문서](../../b-work/B12_S5_CONSUMERS_20261108.md)와 [고정 근거의 boot_artifact_IO_followup](b3-validation-20261108.json)에 연결한다. 실제 transport는 CPU role-I/O fixture이며 현재 Kubernetes/worker·등록 생산자 실증이 아니다. 원43 state·FAIL/UNKNOWN·Ref와 실제 연동 잔여는 유지한다.

세션6은 고정 코드·함수 AST, 3실패/고유28통과의 원 JUnit/argv/freeze, 원/수정 Store6개의 EVD/RES12객체 해시, manifest465개 중 정확2해시 변경을 읽기 전용으로 대조했다. 범위내 미해결 구체 결함은 없었고 시험·API·모델·복원 재실행은0이다. 이 후속 head의 원격 CI는 게시 후 확인한다.
