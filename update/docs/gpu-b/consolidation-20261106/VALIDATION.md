# 통합 검증 — 2026-11-06

## 현재 통합 소스

- main `e5a8ba7589be09fa472c35276e190b117426a0ae` 위에 기존 미반영7개 commit을 적용했다. 원 commit 순서는 `97cf8cfc`, `3a115bd6`, `ee44d8dd`, `4e4da648`, `678a20d8`, `58ee6dff`, `3b3ff137`이다.
- 충돌은 `reviewed-source.json`에서만 발생했다. 기존 생성기로 새 tree의 실제 bytes를 고정하고, 현재 main의 파일을 누락하지 않은 채 447개를 검증했다. manifest SHA는 `26a40d525351afb223f82f42852f2462c4b21089004ddbd583a658810ab4efe4`다. 런타임에서 해시를 승인하거나 입력을 제외하지 않았다.
- 통합 호환 수정 `6897883e`: LIF-14 DRAFT builder는 정확한 baseline32와 main baseline33만 받는다. 두 판의 LIF-14 질문/judge YAML 차이는0이다. 다른76개 정의의 bytes와 원 질문·judge를 유지하는 기존 검사 및 미래34 거부 검사를 포함했다. PACK 원본·판정 enum은 변경하지 않았다.

## 직접 영향 회귀

Linux/WSL CPython **3.12.14**, 현재 `uv.lock`의 offline/frozen 환경에서 **201 passed / 실패0 / 새 skip0**, pytest 벽시계 **42.23초**다. 모의/파일 계약 회귀이며 엔진·Docker·network 표식의 기존 기본 제외는 유지했다.

```text
python -B -m pytest -q
  tests/test_profiler_owned_trace.py
  tests/test_lif14_profiler_control_pack.py
  tests/test_lif14_stop_restart_prepare.py
  tests/test_plt06_prepare.py
  tests/test_gpu_b_sup04_realmodel.py
  tests/test_profiler_lifecycle_cpu.py
  tests/test_kubernetes_profiler_control.py
  tests/test_lifecycle_capture.py
  tests/test_a1_profiler_sensor.py
  tests/test_a1_profiler_product.py
```

시험 source는 이 PR의 commit snapshot이며, 임시 출력은 해당 source root 밖의 자기 소유 Linux 시험 디렉터리를 사용했다. 전체 suite·wheel·모델·GPU 재실행은 이 통합에서 하지 않았다. 원 90초 SUP-05 시험과 이 pytest 시간은 다른 작업이다.

## 초기 실패와 수정 범위

- Windows 최초 실행은 148 passed/52 failed였다. 디렉터리 FD·POSIX `O_NOFOLLOW`/UID·mode 처리, source 내부에 둔 시험 출력의 보호 조건, baseline32 고정의 문제가 포함돼 있다. Windows 실패를 제품 PASS로 바꾸지 않는다.
- 최초 Linux 실행은 172 passed/28 failed였다. 27개는 시험 출력을 보호 source 내부에 둔 조건, 1개는 baseline33 미지원이었다. source 보호를 완화하지 않고 시험 임시 위치를 source 밖으로 옮겼으며, 정확한 baseline33 SHA만 명시적으로 지원했다.
- 수정 후 위 동일 관련 파일 전체를 다시 실행해 201개 통과를 확인했다. 기존 assert·실측 기한·관측 필수 조건·수락 기준을 약화하지 않았다.

## 구현 사본과 증거 포인터

- `evidence-index.json`의 기존43개 ID가 유일하며 원 index의 ID 집합과 같다. source pointer의 SHA는 원 파일에 연결하고 project-relative 경로로 표시한다.
- 고정 구현 사본17개의 bytes SHA가 원 고정 파일과 일치하고 Python AST parse를 통과했다. `.gitattributes`는 이 사본의 줄바꿈을 보존한다. 사본은 실행 배정·spec·ENV/target/prepared·helper를 포함한 완전한 실행 패킷이 아니다.
- 이전 CPU 3/11 계약·원 archive·CRC·Ref 복원 검증은 해당 세션 결과를 재사용했다. 새 통합을 이유로 반복하지 않았다.
- 실제 SUP-05 실패의 원값·정리·최종 index는 세션6의 완료 결과를 참조한다. 새 SUP-16의 잘못 선택된 GB07 파일은 GB09 증거로 사용하지 않는다. 정확한 반출과 제품 경로 확인은 다음 단계다.

이 PR은 43개 실증 완료·정상 판정·공식 GPU MEASURED·배포 승인을 주장하지 않는 Draft 통합이다. 로그·JUnit·비공개 원자료 포인터는 통합 worktree의 `out/consolidation-20261106/` 및 각 원 소유자의 고정 경로에 보존한다.
