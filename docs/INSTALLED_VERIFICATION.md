# 설치된 wheel의 오프라인 계약 검증

`uv build --wheel` 뒤 다음 명령을 실행한다. 출력은 저장소 밖의 **새 경로**여야 한다.

```powershell
uv run python tools/verify_installed.py --wheel dist/guard-0.1.0-py3-none-any.whl --output C:/guard-verification/new-run
```

Linux에서도 같은 옵션을 사용한다. `--output`을 생략하면 운영체제 임시 폴더 아래 새 경로를 만든다. 기존 폴더를 덮어쓰거나 삭제하지 않는다. Python 3.11과 PATH의 `uv`가 필요하며, `--uv`로 실행 파일을 지정할 수 있다.

감독 프로세스는 `uv.lock`의 **런타임 의존성**을 export하고 별도 가상환경에 의존성과 지정한 wheel을 설치한다. 이 설치 단계는 패키지 캐시 또는 패키지 저장소를 사용할 수 있다. 제품을 시험하는 worker 단계와 구분한다. worker는 저장소 밖 CWD와 `Python -I`로 실행하며 제품 네트워크·Docker·자식 프로세스 호출을 차단한다. `tests/`·`tools/`를 제품 모듈로 import하지 않는다.

검증하는 계약은 다음과 같다.

- 현재 소스의 패키지 파일, wheel 파일, 실제 설치 파일의 bytes 일치와 설치 경로
- 주요 CLI help와 EXAMPLE fake의 select → run → judge → JSON/HTML report
- 반출 → 새 위치 복원 → 현재 디스크 팩 없이 저장 팩으로 judge/report
- 원본 저장소 보존과 변조 탐지
- REQ-15의 정책 등록, EXAMPLE 2그룹 양방향 6case의 제한적 수동 PASS, 다른 entry 첨부·불완전 증거의 PASS 보류, 신고 FAIL 보존, 부분 선언·legacy 구분
- 준비 입력 파일 누락의 구조화된 원인·side·안내와 원본 보존
- 저장 증거 분석 진입점의 읽기 전용 처리, EXAMPLE OBS-03 로그 재검증 일치, 불충분한 증거·변조 거부

결과는 `summary.json`에 기록한다. 합성 fixture와 가짜 실행기의 시험이며 실제 엔진, 실제 캐시 격리, 두 이미지 업데이트 실증이나 기업 승인 검증이 아니다. EXAMPLE 실행을 MEASURED로 바꾸지 않는다.

CI의 기존 기본 pytest와 wheel 빌드는 유지하고, 그 뒤 이 설치 검증을 실행한다. 제품 pytest는 세부 실패·경계 사례를 담당하며 설치 검증은 패키징·진입점·재현 가능한 핵심 흐름을 담당한다.

기본 테스트의 I/O를 줄이기 위해 `tests.builders.baseline_pack()`은 검증된 테스트 기준 팩을 한 번 로드한 뒤 매 호출 깊은 복사본을 준다. 제품 `load_pack`은 변경하지 않는다. 팩 파일 변조 시험은 복사한 경로를 제품 로더로 다시 읽는다. 순수 업데이트 수락 시험은 메모리 객체 그래프를 사용하고, 실제 저장소·반출/복원·CLI 통합 시험은 각자의 디스크 경로를 유지한다. 공유 mutable 객체·테스트 순서·오래된 제품 캐시에 의존하지 않는다.

이번 최종 wheel87파일은 소스·wheel·설치본 bytes가 일치했고 위 오프라인 계약을 통과했다. Windows 기본2551통과/13skip/18제외와 기준 공통 집합의278.39→235.77초 단일 측정은 [묶음 검증](POLICY_RELIABILITY_BATCH.md#검증-기록)을 따른다. 전체1분 목표는 아직 미달이며 검증 범위를 줄이지 않았다.
