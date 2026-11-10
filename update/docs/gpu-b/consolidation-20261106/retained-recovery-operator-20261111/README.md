# SUP-16 GB12 회수 준비 소스

보관된 GB12 원 archive를 압축 포장해 회수하는 B 운영 준비 코드다. 독립 검토를 마친 원 파일을 바이트 변경 없이 보존했다. 일반 제품 코드·엔진 생산자 등록·실측 PASS가 아니며, 이번 준비에서 GPU 기동·AWS 명령·실제 GET·모델 실행은 수행하지 않았다. 원 R8의 실제 API 성공·실패·UNKNOWN·정지 관측은 [운영 기록](../SUP16_ACTUAL_OBSERVATIONS_20261111.md)에 별도로 남아 있다.

[source-manifest.json](source-manifest.json)의 `staging_files`가 고정 파일명, 저장소 경로, bytes·SHA를 연결한다. 9개 원문을 이 폴더에 보존했고, 기존 읽기 전용 SDK child와 archive codec는 `update/tools/`의 동일 바이트 파일을 참조한다. `.gitattributes`는 원문의 줄바꿈을 유지한다. manifest의 SHA는 배포된 실제 파일을 확인하는 기준이며, 로컬 폴더 이름을 Git 커밋으로 잘못 표시하지 않는다.

## 연결 구조

1. `private_loader.py`는 UID1000·private700·REG600·단일 링크·같은 FD의 bytes/SHA/신원을 확인하고 모듈을 구성한다. 문맥을 나갈 때 지정 모듈 5개와 FD를 정리한다.
2. 새 `aws_cli_adapter_v2.py`는 원 adapter를 별도 namespace에서 구성해 `OUT`을 새 private의 `CLI`로 바인딩한다. 부모와 기본 script-directory 방식으로 import하는 전송 자식이 같은 저널을 사용한다. 원 adapter·GET·REMOTE guard와 SDK child는 변경하지 않는다.
3. `collector_glue.py`는 원 요청 SHA, 서버 수락의 문서명·명령 인자·대상/CID, 같은 대상의 Success0 terminal과 완전한 encoder 영수증을 연결한 뒤 GET 입력을 만든다.
4. `recovery_controller.py`는 원 health 신원→encoder→검증된 영수증→기존 GET→원 정상 stop/관측을 연결한다. terminal 진단이 실패해도 최초 오류를 보존한다. 불확실성이나 정지 관측 미완료는 잔여로 남는다.
5. `invoke_recovery_once.py`는 고정 packet과 로컬 actor 결과를 연결한다. outcome의 필수 값·형식을 모두 검사한 뒤 상태를 반영하며, 미검증 자료에서 정지 확인 잔여를 없애지 않는다. 옛 driver의 `main/start/execute`는 이 새 회수 흐름에서 호출하지 않는다.

실행기 소스는 기존 CloudShell의 Python3.13·UID1000/GID996 프로필을 고정한다. loader는 별도의 native Python3.12.3에서 실제 파일·모듈 소비를 검증했다. 실제 CloudShell 점검은 아래 원영수증의 범위로 구분한다.

## 검증 범위

[verification-scope.json](verification-scope.json)에 원영수증·독립 검토 SHA와 범위를 연결했다.

| 범위 | 확인 결과 | 한계 |
| --- | --- | --- |
| 수락 명령 결합 | 수정 영향 합성6, 독립 검토 완료 | 원22 결과를 수정본의 전체 재시험으로 옮기지 않음 |
| controller 최초 오류 보존 | 원 모의9 보존, 수정 영향 모의5·독립 검토 완료 | 실제 health·SSM·GET·정지 아님 |
| loader·shim | 실제 native 파일/모듈·무해 자식 import 14경계, 별도 실제 로컬 합성 저널 소비 2경계·독립 검토 완료 | 원 helper main·CLI/API·controller·codec 실행 아님 |
| invoker outcome | 원 모의7 보존, 수정 영향 모의5·독립 검토 완료 | Popen/wait·PID·정지 상태는 명시적 대역 |

이 수치는 준비 계약·변경 영향 범위이며 GPU43 ID나 제품 완료 수로 합산하지 않는다. 구 실패 source·불완료 하네스 시도·원 영수증은 기존 보존 위치에 남긴다. 실제 AWS UNKNOWN 요청을 합성 저널로 해소했다고 표시하지 않는다.

## 남은 실제 연결

2026-11-11 07:39:38 KST 기존 CloudShell의 새 private 폴더에서 실제 파일 준비와 `check` 모드를 한 번 수행했다. Python3.13.16·UID/EUID1000·GID996·`-I -S -B`, 종료 코드0이다. 소스11개와 packet1개의 REG600·소유자·단일 링크·bytes/SHA와 전후 metadata, private700, FD 집합0·1·2·3 유지를 대조했다. stdout은 1,298B이며 stderr는 비어 있다. CLI 디렉터리와 운영 actor는 생성하지 않았다.

[원영수증](actual-source-check-receipt.json)의 SHA는 `55b0a1df9280e1006d8886d965fdb516ef2aa02cf31703fe3c090ca70fdaf025`다. [저장소 manifest와의 연결 대조](actual-source-check-review.json)를 함께 보존한다. 원 R8 경로 비접근 표시는 영수증 선언·검토된 경로 제한 범위이며, 이 점검에서 EC2 원 archive를 새로 조회하거나 전체 무결성을 검사하지 않았다.

candidate packet은 `not_ready=true`, `execution_ready=false`, 실제 boot·CID null을 유지한다. 이 실제 `check` 성공은 운영 actor·health·GET·GPU 실행 성공이 아니다.

guest lzma 지원과 실제 GB12 압축률, encoder 수락/종료, 전체 전송·새 파일 복원, 원 14,636,464B/SHA 일치, ZIP 첫 전체 CRC·의미/Refs, 모델 정상·부분 파일·복구 실증은 아직 미완료다. 원본·이전 partial을 덮어쓰지 않고 원 FAIL·UNKNOWN·null·정확 Ref와 제품528/실제 GPU466 파일 구분을 보존한다.
