# LIF11 선정 자원 회수 관측 제품 계약

원팩31 LIF11의 SC18-02 실행 중 실패·SC17-01 초기화 부분 실패 두 분기와 정상 종료/자원 회수/같은 시험 환경의 후속 기동·정상 요청 기준을 유지한다. 부모 A39/56/4·A1 19/27/2·공동c9af729와 PR125 확정9e96929·기존 미커밋/worktree·sources/·A2/B 원본·과거팩/정확Ref·실증을 보존한다. 이 묶음은 선정 자원 census와 명시 절대 기한 위반의 제품 연결이며 전체 두 분기/부모 Goal 완료가 아니다.

`resource_recovery:kubernetes` 0.1.0, collection `kubernetes-selected-resource-recovery-v1`을 기존 select/run/judge/report/verify/export/restore에 연결한다. `tools/build_kubernetes_resource_recovery_pack.py --seed reference/pack-seeds/PACK-BASELINE-31 --output <새 외부 폴더>`는 LIF11만 명시 DRAFT로 바꾸고 원질문·정상·FAIL/UNKNOWN·두 시나리오·다른76개 정의 bytes를 유지한다. core/models/store/judges·외부 contracts와 루트 runtime은 변경하지 않는다.

선정 vLLM0.31 mp의 기존 native13(runtime-failure) 또는 native14(partial-initialization) 준비 manifest를 명시 소비한다. 기본 native8~14의 원래 source/bytes·현재 PID/start·source/image/lab 관문과 native0.32 미지원은 유지한다. 종료 기록이 남았어도 현재 프로세스 결합이 없으면 기존 의미대로 미확인이다. kernel helper는 별도 exec 수집 주체이며 serving native hook이 아니다. native 원기록을 kernel 소유자 인증이나 실제 모델/TP/GPU 소비로 승격하지 않는다.

새 공개 `KubernetesResourceRecoveryReader.resource_census(selection, max_clock_skew_ms=...)`는 기존 소유 lifecycle reader의 namespace/Pod/container/image/restart/start/run·ENV·전후 확인을 재사용한다. 고정 stdlib helper `resource_recovery_probe.py`의 검토된 파일 bytes SHA256은 `86f51b71a70b494562c04bb518afdab420a4f70d2577d6f7f205cec5a067240c`이다. 실행 중 읽은 hash를 승인값으로 쓰지 않는다. 입력은 bounded stdin이며 임의 경로·명령·모듈·콜백을 받지 않는다. 각 exec 최대5초, 입력64KiB/출력256KiB,64 owner/128 descriptor, 전체300초 이하를 요구한다.

selection은 `guard-resource-recovery-selection/1`의 닫힌 owners/resources 목록이다. owner는 불투명64hex id·rank·PID/start_ticks/real UID/boot hash·PID/IPC/mount/time namespace 및 time-offset hash·명시 require_absent를 갖는다. 같은 선택의 namespace/boot/time 좌표는 일치해야 한다. resource는 불투명 id·owner_id·FD 번호·unix-socket 또는 posix-shm-fd·device/inode·명시 require_absent만 갖는다. PATH·환경·argv·통신 내용·파일 본문은 없다. 중복 owner/rank/PID/FD·bool 정수·미확인 종류/필드는 거부한다. 선언한 필수 rank와 선택 목록도 대조한다.

고정 helper는 선정 PID의 stat/status/namespace와 선정 `/proc/<pid>/fd/<fd>`의 metadata만 전후 읽는다. POSIX shm FD는 regular mode와 `/dev/shm/` 연결을 메모리에서만 확인하며 경로를 출력하지 않는다. 전체 process/FD scan, connect·signal·poll/wait/join·자원 변경이나 내용 읽기를 하지 않는다. 소유 프로세스 match/absent/replaced/unstable/unavailable, descriptor match/absent/replaced/unsupported/unavailable/owner-unavailable을 구분한다. 권한 거부/수집 실패를 부재로 바꾸지 않는다.

선정 FD가 사라져도 다른 FD·다른 process의 alias, mapping·queue·공유 참조가 자원을 계속 보유할 수 있다. PID/FD 재사용은 원세대의 잔존/안전 회수로 채우지 않는다. process가 사라져 descriptor를 읽을 수 없으면 owner-unavailable이며 전체 IPC/shm/GPU 반환으로 처리하지 않는다. 항상 global_resource_release_verified/gpu_release_verified/producer_authenticated=false다. 컨테이너가 종료/교체돼 exec할 수 없으면 누락이며 과거 UID/미래 대상/다른 Pod 로그로 보충하지 않는다.

15개 필수 정책은 `resource_recovery_contract.POLICY_TYPES`가 정본이다. ENV/target/prepared/selection/scenario의 정확 hash, aware600초 이하 유효창, 두 분기 중 선택·mp·필수 rank·선언 phase(before-failure/after-cleanup/restarted-normal), 기한 근거·절대 회수 시각·명시 허용 clock 오차·요구 자원 종류를 입력한다. 정확 모델 inventory/revision/API 별칭도 ENV/target에 있어야 한다. 정책이나 실제 owner/PID/inode/hash를 실행 중 자동 승인/보완하지 않는다.

기한 근거 `absolute-unconditional`은 해당 선정 주체가 명시한 절대 시각 이후 없어야 한다는 무조건 정책이다. 두 census의 동일 소유자/세대/시계와 선정 process 또는 FD/inode를 확인하고, 허용 clock 오차까지 지나 원대상이 여전히 남아 있으면 `recovery_absolute_deadline_violated=true`를 제공한다. 기존 rule_table의 functional FAIL만 연결하며 보안 공격·실패 원인·전체 회수 인과를 확정하지 않는다. `after-verified-failure`는 독립 실패 trigger/clock이 아직 없어 이 경로에서 위반을 확정하지 않는다. 선언 phase/scenario·near-time만으로 조건을 채우지 않는다.

별도 값 `recovery_selected_absence_observed`는 명시 선정 주체의 국소 부재이며 PASS 조건이 아니다. 정상·실패의 전체 자원 목록/소유자/반환 시각·공유 참조·GPU·실제 모델 후속 기동/정상 요청 lineage가 없으므로 전체 범위와 next_normal_launch/global_resources_released=false, normal_control.succeeded=null이다. 그 근거가 없는 축은 UNKNOWN이다. 정상 대조를 기한 위반 유무 한 가지로 축소하지 않는다. 명시 절대 기한 위반으로 확정한 독립 FAIL은 후속 native/seal/첨부/부모 정리 오류가 지우지 않아야 한다.

HTTP는 기존 초기화의 닫힌 절차를 별도 `KubernetesRecoveryHTTP.perform(before|after, request_id)`로 재사용한다. 같은 UUID의 byul-lif11 요청으로 고정 POST `/v1/completions`, 정확 API 별칭·hello·max_tokens1·temperature0·streamfalse만 보낸다. 새 명시 프로필에서만 running engine Ready=false를 관측하고 소유 probe는 Ready=true를 별도 요구한다. 전후 세대·응답 ID·출력/시간을 검증하고 원문 요청/응답/오류·예외·주소·자격을 저장하지 않는다. HTTP200은 재기동이나 자원 회수 증명이 아니다.

설정은 `[executors] resource_recovery="kubernetes"`, `[adapter] name="vllm"`, `[resource_recovery] target="..." prepared=".../manifest.json" selection="..."`이다. 선택 kubectl_binary/max_duration_seconds 외 설정을 거부한다. native13/14 prepare는 기존 명령을 재사용한다. 실행 순서는 최초 census → 두 제한 요청 → 최종 census → 선정 session seal/capture → 정제 EVD/RES/RPT/verify/정확Ref 반출·복원·재판정이다. 장애 활성·정리·재기동 제어 API/신호/원격 변경은 수행하지 않는다.

검증은 같은 공개 경계의 EXAMPLE 응답으로 두 분기의 정상 부재·명시 잔존 위반·조건부/미래 기한·clock/신원/교체/restart·누락·FD/PID 재사용·후속 seal/native/첨부/부모 정리 실패와 독립 FAIL 보존을 확인한다. 소유 Linux CPU의 실제 socket·shm FD·alias·child를 따로 검증하며 선정 FD 부재 후 alias가 살아 있는 반례를 유지한다. 이 사례와 Windows의 소유 pipe 어댑터는 실제 serving/TP/GPU 실증이 아니다. 새 skip/제외나 근거 없는 전체 재실행을 추가하지 않는다.

원GPU창종료·추가Start0·마지막 확인stopped·기존46.66USD/과거 사용량·보존120GiB EBS 과금·CI 결제 차단을 유지한다. 실제 재개에는 새 시간 승인과 현재 자원/활성작업/quota/잔여총비용·정확 ENV/POL/model/source/image·선정 자원/주체/시계·안전 실패/복구·다음 정상 기동 및 정리 책임이 필요하다. LIF08 감지/전달·LIF09 준비 집계·B LIF07 readiness 판정과 구분한다.

첫 고정 시험에서 새 census가 초 단위 utcnow로 나노초 helper 측정을 감싼 결함을 확인했다. 관측 값은 같은 초의741ms였는데 host 끝이0ms로 잘려 명시100ms 오차에서도 미확인으로 처리됐다. 이 새 경계만 time.time_ns의 전후 bracket과 정밀 ISO 시각으로 보수하고 전역 시계·스트림 계산·정책 오차는 변경하지 않았다. 시험 응답의 Namespace kind를 기존 계약대로 제공하고 Legacy 구조는 typed attribute로 읽도록 보수했다. 기존 검사를 없애거나 정상/위반 기대값을 낮추지 않는다.

검증 마감: 정확 source f1bda4d9a0d726644defaaa005437038948bbe21, 검토 고정 공통 소비c0686ab20d5653091754570853472f9880930dc2. 첫 Windows33실패426통과/Linux33실패427통과와1273파일 명세를 보존했다. 새 census clock 정밀도·시험 Namespace/Legacy 보수 후 Windows194통과/기존skip1·deselect1(98.93초/부모99.812초), 동일1273파일 Linux195통과/기존deselect1(29.46초/부모29.718초), 소스-wheel-격리설치222파일 일치·설치본 두 분기4사례/정확24Ref 왕복8.226초를 확인했다. source/hash 고정 뒤 변경0·새skip/제외0·승인값 자동채움0이며 공통330파일/경로제외0·A2선정12/B원본27/native8~14를 보존했다. 바뀌지 않은 인접 통과를 재사용하고 전체suite를 새로 통과했다고 주장하지 않는다. 실제 소유 Linux CPU socket/shm/alias/child census와 두 분기의 무조건 절대 기한 잔존 FAIL/조건부UNKNOWN/누락/재사용/교체·후속 수집/첨부/부모 정리 오류 뒤 독립FAIL 및 정확Ref를 검증했다. 이 근거는 CPU/EXAMPLE이며 실제 serving/TP/GPU·실패 후 전체 회수/후속 정상 기동·부모A39/56/4·A1 19/27/2 전체 완료가 아니다. 원GPU창종료/Start0·기존비용/EBS과금·CI결제 외부차단과 과거실증source04f7856을 유지한다. 확정 커밋/PR·정확HEAD CI 및 검토한 최종 파일 bytes는 A1 인계에 기록한다.
