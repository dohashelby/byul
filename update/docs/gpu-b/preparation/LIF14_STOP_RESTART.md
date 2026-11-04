# LIF-14 격리 종료 오류와 복구 뒤 새 수집

실행 묶음은 COORD `prepare/LIF14-stop-restart/<버전>/packet.md`의 고정 commit·manifest·검증 근거를 따른다. 기본 PACK32와 과거 M07·native15 자산·결과를 덮어쓰지 않는다. 새 실행은 새 자산과 새 출력 경로를 쓴다. native22와 섞지 않는다.

## 새 직접 관측과 한계

`profiler_observation:kubernetes` → 기존 `kubernetes-profiler-control-v1` factory를 재사용한다. native15의 `guard-native-profiler/2`는 검증된 vLLM `_start`/`_stop` 호출에서 실제 Torch C builtin `_profiler_enabled()`를 읽는다. 같은 worker/PID-start·wrapper·backend 객체·native thread의 비활성→활성 수집 세대와 종료 후 상태를 결합한다. wrapper flag, API200, 호출 횟수로 수집 종료를 만들지 않는다. 다른 thread·교체·미지원 probe는 해당 사실을 미검증으로 남긴다. event/1과 당시 runtime 계약은 읽기 호환만 유지하고 새 사실을 소급하지 않는다. 새 관측 runtime은 실제 계산한 CPython3.12.3/3.12.14다.

산출물은 session 전용 경로의 nofollow·동일 UID·안정된 inode를 읽고 trace JSON/gzip을 확인한다. 최대16파일, 원파일 합8MiB, gzip 해제16MiB다. 이름·trace 본문·예외 텍스트는 증거에 넣지 않는다. 한도를 넘으면 미검증이며 기존 기록기2048행/2MiB, 행16KiB 한도를 올리지 않는다.

소유 marker 영수증·native stop의 실제 충돌·그 marker의 보존 복구·새 backend 세대/산출이 함께 있어야 오류·복구 검증을 표시한다. backend가 이미 꺼진 뒤 trace 출력만 실패한 경우를 계속 수집으로 오판하지 않는다. 확인된 wrapper 종료 표시/실제 수집 지속 위반은 후속 수집 오류·교체에도 보존한다.

API→worker RPC의 인과 결합, 전체 worker/자원 roster, 산출 전송·전체 기한 충족은 별도다. `scope_complete=false`를 유지한다. DRAFT는 원 PACK32의 질문·정상·challenge·FAIL/UNKNOWN·judge를 그대로 두고 직접 관측 키만 추가한다. 기존 judge의 축도 보존하므로 CPU EXAMPLE의 기계적 FAIL은 제품의 보안 결론이 아니다. 전체 질문 완료나 보안 PASS를 주장하지 않는다.

## 준비와 실행

기존 M07 canonical-reference의 `normal.rendered.json`과 모델·chat manifest를 경로로 재사용한다. 준비 도구 `tools.lif14_stop_restart_prepare`가 기존 template/model/chat SHA와 고정 소스/정본 manifest를 확인하고 새 native15·DRAFT·engine.json을 만든다. 실행할 수 있는 소스의 `update`에서 UID1000으로 실행한다. 임의 chown·의존성 설치·배포·정책 생성은 하지 않는다.

```text
uv run python -m tools.lif14_stop_restart_prepare --source-commit <packet의40자리commit> --manifest-sha256 <packet의SHA> --template <M07/normal.rendered.json> --model-root <검증된model경로> --model-manifest <기존model-manifest> --chat-template <기존chat> --output <새외부준비경로> --namespace <이번소유namespace> --run-id <이번run> --node <실제GPU노드>
```

생성된 준비 영수증은 source/asset/pack 결합만 뜻한다. 실제 ENV/target/POL·유효창·승인 ref·Pod UID/PID/boot/GPU 신원은 세션2가 결합한다. 기존 기업 값과 null/confirmed_by를 임의로 채우지 않는다. 새 profiler 설정은 기존 torch/ignore_frontend/TP1을 유지하고 trace mount만 `/guard-profiler-traces`로, 출력은 그 아래 `<prepared session>`으로 결합한다. 준비할 때 session trace 경로는 없어야 한다. 실제 fixture가 정상 시작 뒤 종료에서만 경로 충돌을 만나도록 정규 파일을 생성한다.

cfg의 `owned_trace_fault=true`는 `profiler_case=prepared-stop-failure`, required ranks `["0"]`, 전용 소유 scope와 정확한 ENV/target/prepared 정책에만 허용한다. 다른 값은 기존 검증에서 거부한다. 순서는 기존 normal-before → start → normal-active → stop → next-start → normal-next → next-stop → normal-after다. marker 활성화는 정상 전 대조 뒤, 보존 복구는 첫 stop 뒤다. 복구가 확인되지 않으면 새 start를 보내지 않는다. 기존 HTTP 단계10초·설정된 전체 기한·정책600초 상한·준비/Pod/EC2 운영 기한은 유지한다.

marker 복구는 `runtime/profiler_owned_trace.py`의 폐쇄된 arm/recover만 사용한다. 정확한 UID/dev/inode/marker를 검사하고 Linux `renameat2(RENAME_NOREPLACE)`로 원파일을 `<session>.fault-record`에 보존한다. 교체 파일·symlink·hardlink·기존 디렉터리·기존 보존 파일은 지우거나 덮어쓰지 않는다. 복구 오류 때 보존 marker와 진단을 반출하고 세션2의 기존 소유 namespace 정리 절차를 따른다. 기존 EC2/EBS 보존 계약을 바꾸지 않는다.

선정·run·judge·report·verify·export/restore·저장팩 재판정은 기존 guard CLI와 세션2 절차를 그대로 사용한다. `tools.build_kubernetes_profiler_control_pack`이 생성한 DRAFT를 선택한다. 과거3bba 소스에 고정된 M07 실행 스크립트는 새 소스 검사를 제거해서 재사용하지 않는다.

## 검증·인계

CPU 직접 계약은 `test_profiler_owned_trace.py`, `test_lif14_profiler_control_pack.py`, `test_lif14_stop_restart_prepare.py`와 기존 profiler/LIF14/OBS02 회귀다. 별도 `tools.lif14_backend_contract_cpu`는 기존 실제 Torch 환경에서 CPU 연산을 수행해 pinned vLLM method → 실제 C probe/trace → 기록기/JSONL 검사 → 소비/factory → EVD/RES/RPT → 별도 DB 복원/저장팩 재판정을 검증한다. API/K8s/physical-binding 경계는 EXAMPLE이며 GPU 실측이 아니다. 정상·trace 충돌/복구·계속 수집·후속 오류와 교체/미지원의 정확한 Ref는 각 실행 receipt에 보존한다.

필수 관련 검증·소스 고정값·통합 전체 CI·wheel·격리 설치와 세션3 검토의 고정 결과가 준비되면 세션2에 packet을 인계한다. 이미 유효한 같은 코드 검증은 재사용한다. AWS·실측·비용·정본 checkpoint는 세션2 소유다.
