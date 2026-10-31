# CPython 3.12.15 공통 관측 ABI / 1

CI의 `setup-python: 3.12`가 실제 CPython 3.12.15를 선택하면서 기존 고정 런타임 gate가 거부했다. PR166의 default 결과(615 실패·8553 통과·169 오류·19 제외)를 이 원인 하나로 설명하거나, 공통 ABI 지원만으로 CI5 성공을 선언하지 않는다. CI 설정·검사·결제·한도를 변경하지 않는다.

## 지원 계약

- 새 **생산자** 범위는 CPython 3.12.15 / Linux / x86_64 / optimize=0이다. 기존 CPython 3.12.3 POSIX와 3.12.14 Windows 계약은 유지한다. 새 런타임의 다른 OS·machine·최적화와 미검토 patch 버전은 거부한다.
- 관측 profile과 사건 schema는 변경하지 않는다. 기존 exact source·code·library·image·lab gate와 native0.32 미지원 범위를 유지한다. source/module/code가 다른 경우 거부하며, 실행 중 읽은 해시를 승인값으로 사용하지 않는다.
- metadata parser와 `vllm_online_sensor.runtime_contract(runtime)`의 **명시된 수신 runtime**은 수집 호스트와 분리한다. Windows 수집기가 Linux 3.12.15 자료를 검증할 수 있다. 이 입력 해석이 현지 생산자 설치 권한을 뜻하지 않는다.
- `runtime_contract()`의 현지 생산자 선택은 새 OS/machine 범위를 검사한다. 기존 각 sensor의 `configure(...)`도 설치·base 변경 이전에 새 범위를 검사한다.
- Linux 3.12.15의 파일 ctime 의미는 `change`이며, 기존 Windows 3.12.14 path의 `creation` 의미를 확장하지 않는다.

## 고정값의 근거

부모 `3edb6ca39f99d17d18a6ad699d197adb40bb8635`의 실제 bytes를 보존했다. 원본 50개 고정 소스 전체 코드 그래프, 관측기 계약 13개, 표준 연결 메서드 4개를 Windows 3.12.14·Linux 3.12.3·Linux 3.12.15에서 대조했다. 새 3.12.15는 기존 검토 3.12.14 값과 모두 일치했다. 따라서 기존 검토 값에서 새 버전의 point/callback/module/stdlib 표를 확장했다.

새 생산자 허용 조건이 함수 코드 지문에 포함되는 8개 관측기는 변경된 소스를 세 인터프리터에서 오프라인 컴파일해 지문을 재검토했다. 기존 source/code 검사를 제거하지 않았다. A-1 의존 pin은 검토한 소스 bytes에 따라 의존 순서대로 고정했다. 이 작업은 원격 프로세스에서 읽은 지문의 자동 승인과 분리된다. 새 음성 시험은 다른 OS/machine/미검토 버전의 거부와 메타데이터·최종 tensor/승인 의미의 보존을 검사한다.

공식 인터프리터는 python-build-standalone의 불변 `20261021` 배포에 포함된 `cpython-3.12.15+20261021-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz`다. 자산 663544370 / 34,479,606 bytes / SHA256 `6d7521138fd33afddc1ccd08abb007d9a2829e38117c2576f9efdc0ec73ce772`을 실행 전에 확인했다. 전용 비기본 WSL 경로에 두고 기존 승인 패키지를 읽기 전용으로 재사용했다. 기존 환경·프로젝트 의존성·C CPU 검증 환경·AWS를 변경하지 않았다.

## 전용 소비와 검증 경계

A-2의 `native_weight_iteration`, `native_model_file_reads`, `native_model_mappings`, `native_worker_model_files` 원본과 B의 source approval은 바꾸지 않는다. 이 소비기의 기존 source pin은 새 공통 bytes를 거부해야 한다. A-1은 확정 공통 커밋·파일 bytes hash·직접/설치/제품 경로 검증을 인계하고, 소유자는 새 pin을 검토한 확정 전달물을 제공한다. 그 전 전용 후보가 미연결인 상태와 공통 관측 사실·UNKNOWN·독립 FAIL을 각각 유지한다.

일반 EXAMPLE 시험·컴파일 대조·설치 검증은 실제 serving/worker/backend/GPU·최종 tensor 소비·실제 승인 증거가 아니다. 기존 승인된 Torch/safetensors C CPU 5개+delta1 근거는 PR164이며 이 ABI 변경에 소급하지 않는다. 전체 Goal과 OBS02 승인 생산자 연결도 완료되지 않았다.

## 재현과 인계

직접 영향 시험은 새 `tests/test_a1_native31215_abi.py`와 영향받은 기존 A-1 native sensor/parser 계약을 고정 소스에서 실행한다. 기존 file-read 손상 시험은 새 runtime 승인값도 손상시키도록 확장했으며, point 표의 정확한 key 집합에는 명시한 새 버전을 추가했다. 기존 사례·assert를 제거하거나 skip을 추가하지 않았다.

검증 source, 직접/인접/설치 결과, 기존 CLI·EVD/RES/RPT·verify·반출/복원·저장팩 정확 Ref, 전용 소비 미연결, 정확 HEAD CI5 상태는 묶음 인계에 별도로 기록한다. 기존 전체 suite와 실제 REQ11 `04f7856` 실증은 반복하거나 새 소스의 결과로 바꾸지 않는다.

## 검증 마감 범위

- 최초 고정 source `d8c685b93c7eedbd93df316d944af9041cd0d00a`: Windows 3.12.14·Linux 3.12.3 직접 영향 시험 각각 369 통과. Linux 3.12.15는 265 통과·104 오류였다. 초기 구조화 시험 도구의 spawn/진단 API 오류와 원 결과도 보존했다.
- Linux 오류는 host 정보를 가지지 않은 세 EXAMPLE OS 객체(attempt27·Online37·output28 사례; output28은 후속 재검증에서도 남았던 같은 사례)와 Linux endpoint parser의 누락 12 사례였다. 실제 host 정보만 위임하는 fixture facade를 사용하고, parser는 검토한 두 Linux patch 버전에만 endpoint 지문을 허용했다. 기존 file-name/writer 모의 동작·assert·source/code gate는 유지했다.
- 보수 source `089ebc33f297e553faf9927fc887e3ab4cb57444`: Windows 3.12.14·Linux 3.12.3 영향 143 통과씩. Linux 3.12.15 최초 실패 범위는 76 통과·출력 fixture 28 오류였다. fixture 전용 보수 source `f26f4588cb1f77db8281f1087ecc3d9278ff5aca`에서 출력 시험 전체 37개가 세 인터프리터 모두 통과했다. 이를 새 HEAD 전체 369개 단일 실행으로 표기하지 않는다.
- 인접/불변식·B 원 gate: Linux 3.12.15 142 통과. Windows 135 통과·기존 INV03 skip 1·Git 준비 오류 6을 보존했으며, 짧은 전용 임시 경로에서 해당 6개가 통과했다. 짧은 경로의 첫 실행은 부모 폴더 누락으로 준비 실패한 기록을 별도로 보존했다. 기존 B fixture·gate는 변경하지 않았다.
- 보수 소스의 오프라인 컴파일 재대조는 세 인터프리터 모두 관측기13/소스50/표준연결4에서 불일치 0이다. A-1의 검토 소비 커밋은 `b3e3bb054bc281e270a0e7be885951a479d993d0`이며, 최종 fixture 보수와 문서 마감은 공통 pin 범위의 bytes를 변경하지 않는다.
- wheel source는 `089ebc33f297e553faf9927fc887e3ab4cb57444`, SHA256 `ad5a59481370b5e530dc8aaf37c86feb57cf2bd9ae2ccdc093bae66a3a295093`이다. Windows 3.12.14·Linux 3.12.15 각각 설치 파일 281개 일치, 정식 OBS02 CLI 정상/부분/후속 수집 오류/첨부 오류 4사례·정확24Ref, 원본 반출팩 보존·복원·저장팩 재판정을 확인했다. A2 원 pin 거부와 UNKNOWN을 유지하면서 공통 관측 사실을 보존했다.
- 원본 증거 zip은 Windows `b7bc6241362ba0ee2e665557f6a31c6fee6ca550ea776426f6677e530e016fa7`, Linux `e3acbf114ed3f786d2af0dc3321fffdf50b9dd0567236df0979e1d65ec358123`이며 Linux→Windows 반출 bytes도 일치했다. 정확 Ref·파일 목록·재현 명령과 원 실패 기록은 `A1/native31215-ABI-review/` 인계 근거에 있다.

정확 통합 HEAD CI5는 원격 확인으로만 갱신한다. 전용 A2/B pin 재검토와 실제 serving/worker/backend/GPU 관측은 남아 있다. 준비 세 단계·OBS02 전체·A1 19개·부모 A39개 완료로 승격하지 않는다.
