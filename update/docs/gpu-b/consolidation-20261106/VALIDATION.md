# 통합 검증 — 2026-11-06

## 2026-11-08 추가: 실제 CPU·로컬 컴포넌트 연결

- 기존 모의 결과와 별도로 새 실제 로컬 관측14ID를 확보했다. LIF-08·12·16, OBS-02에는 이번 새 실제 실행이 없다. 전체 제품 질문 완료 수와 구분한다.
- 원0.31 renderer/InputProcessor 새6사례(수용3·표준거부3), SamplingParams 정상/범위 거부, SUP08의 trust-MM 옵션 함수6사례를 실제로 호출했다. renderer4사례/processor5사례로 실제 도달 범위를 구분한다.
- 별도 설치 vLLM0.29 CPU에서 새 극소형 모델로 정상2요청·각2출력토큰을 확인했다. 원0.31 엔진/GPU 성공으로 승격하지 않는다. LLM 호출 직전→Ready 표기57.308초(후속 설정/proc 수집 포함)·실효CPU KV cache1GiB·Worker정상종료뒤내부Core강제종료·부모종료후동일PID/start부재를 원자료대로 기록했다.
- S1 host11component기록 중10completed·PLT02 metadataassert1불충족/exit1을 유지했다. 별도 자기 소유 user/PID/mount namespace의 같은probe4조건은 충족했다. hostUID1000과 namespaceUID0 매핑을 구분하고 host권한/정책을 바꾸지 않았다.
- SUP16은 새파일의 정상2·거부2/자식4실행·16assert 확인이다. 고정writer API/parser 범위이며 전체writer·native worker·제품factory 검증과 구분한다.
- 신규7도구의 Python3.12 구문·CLI help 및 engine OOV 라벨만 고친 AST 동등성을 확인했다. 실제API 입력 사례가 직접 기능 검증이며 기존 모의/52tests·heavy모델/전체suite를 새 성과로 반복 집계하지 않는다.
- 모의·실제·원자료SHA·버전·미실행·GPU/공통지원 잔여는 [상세 결과](LOCAL_ACTUAL_VALIDATION.md)와 [기계 판독 기록](local-actual-validation-20261108.json)에 분리했다. 전체37컴포넌트 기록을37개 항목/실험/제품PASS로 세지 않는다.

## 2026-11-08 추가: GPU18 모의 연동

- 전용 `guard.gpu18_prototype`에서 18개×정상/위반/누락54사례를 실행했다. 모의 계약은 PASS18/FAIL18/UNKNOWN18, 원팩 대조는 정상 PASS16/UNKNOWN2·위반 FAIL16/UNKNOWN2·누락 UNKNOWN18이다.
- 6개 PLAN에서 EVD108·RES108·RPT6을 생성했다. 실제 내용 주소 Store·팩 snapshot·bundle155파일을 반출/복원하고, 고정 `as_of`로 정확 Result Ref와 보고서의 동등성을 확인했다. 모두 EXAMPLE·DRAFT 정책·비공식이며 GPU 추가 기동0이다.
- 독립 테스트14개 통과(25.13초): 저장 객체/Ref·출처·모의 사건·원팩 대조·독립 FAIL·복원/재판정·변조3종 거부·기존 출력 보존·예외 시 registry 복원·CLI. 프로세스·네트워크 차단기의 시도0이다.
- 기존 회귀38개 통과(43.31초): `tests/test_cli.py tests/test_evidence_bundle.py tests/invariants/test_inv_02.py tests/invariants/test_inv_11.py`. 추가 엔진·모델·Docker·전체 suite 실행은 없다.
- 최초 통합에서 directory 팩과 stored 팩의 안내 문구 차이를 발견했다. 최초 보고서부터 저장 팩으로 생성하도록 수정해 동일 경로로 대조했다. 한계 문구 제거·판정 완화·공통 코드 수정은 없다.
- [실행·실제 연동 인계](GPU18_PROTOTYPE.md), [정확 Ref·SHA 기록](gpu18-prototype-validation-20261108.json). 기존 실증25/18은 유지한다.

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
