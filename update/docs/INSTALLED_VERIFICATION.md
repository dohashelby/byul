# 설치된 wheel의 오프라인 계약 검증

`uv build --wheel` 뒤 다음 명령을 실행한다. 출력은 저장소 밖의 **새 경로**여야 한다.

```powershell
uv run python tools/verify_installed.py --wheel dist/guard-0.1.0-py3-none-any.whl --output C:/guard-verification/new-run
```

Linux에서도 같은 옵션을 사용한다. `--output`을 생략하면 운영체제 임시 폴더 아래 새 경로를 만든다. 기존 폴더를 덮어쓰거나 삭제하지 않는다. Python 3.12와 PATH의 `uv`가 필요하며, `--uv`로 실행 파일을 지정할 수 있다.

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

이전 Python 3.11 묶음의 wheel87파일은 소스·wheel·설치본 bytes가 일치했고 위 오프라인 계약을 통과했다. Windows 기본2551통과/13skip/18제외와 기준 공통 집합의278.39→235.77초 단일 측정은 [묶음 검증](POLICY_RELIABILITY_BATCH.md#검증-기록)을 따른다. 당시 전체1분 목표는 미달이었으며 검증 범위를 줄이지 않았다.

## Python 3.12 전환 검증

2026-10-25 사용자 선택으로 현행 지원 범위를 `>=3.12,<3.13`으로 전환한다. 개발·guard·CI·gua가 관리하는 시험 보조 Python은 3.12를 사용하며, 대상 vLLM 이미지의 Python·digest·검토 프로파일은 변경하지 않는다. 기존 3.11 가상환경은 전환 검증 중 복구 수단으로 보존하고 새 3.12 환경과 검증 산출물은 저장소 밖의 새 경로에 둔다. 별도의 3.11용 소스나 제품을 유지하지 않는다.

설정·코드·테스트 코드·문서 초안과 정적 검토를 마치고 LF snapshot을 고정한 뒤 검증했다. Windows x64 CPython **3.12.14**·uv **0.12.11**의 저장소 밖 새 환경에서 `uv sync --frozen`이 성공했다. 기존 3.11 환경은 수정하지 않았다. `uv.lock`의 패키지 19개는 추가·제거·버전 변경 없이 Python 범위와 3.12용 wheel·환경 마커만 갱신했다.

| 이번 직접 검증 | 결과 | pytest / 부모 벽시계 |
| --- | --- | --- |
| Windows 기본 `pytest -q --durations=50` | 3,236 통과 · 실패/오류 0 · 기존 skip 13 · 제외 19 | 412.24초 / 415.168초 |
| Python 3.12 Docker stub 두 위상 | 2 통과 · 실패/오류/skip 0 | 46.18초 / 47.319초 |
| wheel·저장소 밖 격리 설치본 | 제품 104파일 동일, 실제 Python 3.12·Requires-Python 검증 | 별도 실행 로그 보존 |
| 과거 증거·저장 팩 | 25객체 직렬화/해시 동일, 복원본의 저장 팩22 재판정 10개 동일 | 원본 inventory 동일 |
| 같은 vLLM 소스 6쌍 | 3.11/3.12 artifact 바이트·route·entrypoint·선정 근거 동일 | 기존 로컬 cache 읽기 |

최초 Docker stub 2건은 `lab/policy.yaml`의 과거 팩22 참조와 현재 팩23이 달라 선정 전에 실패했다(pytest 20.11초 / 부모 21.044초). Windows TOML 경로를 올바른 형태로 쓰고, 원본 정책의 값·범위는 유지한 채 현재 팩의 정확한 Ref를 넣은 임시 시험 사본을 사용했다. 팩 검증이나 보안 FAIL/PASS 기대값은 바꾸지 않았다. 수정은 기본 제외 대상 Docker 시험 입력에만 영향을 주며 최종 Linux CI는 기본 suite와 Docker 시험을 모두 실행한다. 기존 skip 13은 Windows 루프백·Linux 전용 dirfd/nofollow·symlink 권한 조건이며 새 skip/제외는 없다.

보조 이미지 `python:3.12-slim`은 이번 로컬 실행에서 **Python 3.12.15**, digest `sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704`였다. 격리 stub에서 exposed REQ-01 보안 FAIL·isolated PASS와 양쪽 정상 대조를 확인했다. 이 결과는 EXAMPLE·official false이며 실제 vLLM 실증이 아니다. 기존 5개 컨테이너 상태와 보호 대상 소스·팩·과거 출력 등 217파일의 바이트가 같고, 기존 `.assessment/` 최상위 항목 metadata도 유지했다. 시험 컨테이너·네트워크는 모두 정리됐다.

이번 wheel SHA-256은 `69682375a589caf9ac046811e871ed6b95639c11d8266bb2fc645056fa94bac1`이며 metadata의 Requires-Python은 `<3.13,>=3.12`다. 실제 vLLM 기동·새 vLLM 이미지·모델 수신·GPU 시험은 하지 않았다. 최종 커밋의 Linux 기본 suite·wheel·Docker CI 결과는 이번 초안 PR에 별도로 기록한다. 원본 DB·실행 로그·환경·검증 JSON은 저장소 밖 `<output>/python312-<날짜>/`에 보존하며 커밋하지 않는다.

남겨둔 3.11 표기는 `PROGRESS.md`·정책/자동화 단계 문서의 과거 테스트 환경과 재현 명령, `DECISIONS.md`의 당시 설치 결정, 원본 증거/저장 팩과 호환성 fixture의 사실이다. 해당 기록의 Python 버전·요청/응답·판정·해시·Ref를 3.12로 덮어쓰지 않는다. 현재 설치 설정과 과거 기록을 구분하며, 새 환경에서는 과거 원본을 읽기 전용으로 확인하고 복사본에서 반출·복원·저장 팩 재판정의 보존을 검증한다.
