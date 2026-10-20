# 런타임 작업 상태

최종 갱신: 2026-10-21

## 현재 단계

- 2026-10-21 개발계획서 v1.4(개정일 2026-10-21, 문서 상태: 개발 기준 설계안)가 전달되어 저장소에 반영됐다. 이는 문서 반영이며 구현·시험·팀 승인을 의미하지 않는다.
- 2026-10-19 사용자 신가영이 작성한 개발계획서 v1.0 초안이 전달됐다. 저장소 사본은 `project-context/런타임_통제_개발계획서_v1.0.md`다.
- 사용자 요청으로 직접 개발할 모듈의 언어는 Python으로 선택했다. 실제 환경·연동 계약·정책값 등 남은 결정은 별도 확인이 필요하다.
- 초안 전달은 기술 스택 확정·팀 승인·구현 완료를 의미하지 않는다.
- 현재 요청 범위는 가벼운 로컬 파일 크기·SHA-256 확인 기능 하나의 구현·시험이다. 보안 통제·외부 연동 기능의 구현·집행 검증은 포함하지 않는다.
- 사용자 요청을 Claude가 정리하고 Codex가 구현·수정·시험한 뒤, Claude가 대화형 세션에서 직접 검수하고 최종 보고한다. 필요한 수정·재시험은 Codex가 맡는다.

## 확인한 환경

- GitHub 저장소: https://github.com/yeong-yi/serving-runtime
- WSL 저장소 경로: `/home/bobgy/bobproject/serving-runtime`
- 2026-10-19 Codex에서 `pwd`, `git rev-parse --show-toplevel`, `uname -a`와 WSL 환경 변수를 확인했다. 작업 폴더와 Git 루트가 위 경로로 일치하며, 명령은 WSL2의 Ubuntu 24.04에서 실행된다.
- 원본 맥락 문서 위치: `/home/bobgy/bobproject/project`
- 개발계획서 v1.4 원본: `/mnt/c/Users/bobgy/Downloads/런타임_통제_개발계획서_v1.4.md`
- 개발계획서 v1.4 저장소 사본: `project-context/런타임_통제_개발계획서_v1.4.md`
- 2026-10-21 `sha256sum` 비교 결과 원본과 저장소 사본의 SHA-256은 모두 `a350b0a1746e09d2cba23ed596fe0cc14e972b2f3b7cc3b1e72f195a908dc0e2`로 일치했다.
- 개발계획서 v1.0 원본: `/mnt/c/Users/bobgy/Desktop/bob_project/output/런타임_통제_개발계획서_v1.0.md`
- 2026-10-19 `sha256sum` 재비교 결과 원본과 저장소 사본의 SHA-256은 모두 `c4961b0578e25b016a8eeb491fd8a8776778b6d5ee43a2b22f96fb7911810c9b`로 일치했다.
- origin: `https://github.com/yeong-yi/serving-runtime.git` (Git 설정으로 확인)
- 사용자가 GitHub CLI 로그인과 VS Code에서 이 저장소를 연 상태를 확인했다.
- 2026-10-19 사용자가 전달한 Claude 응답으로, 새 저장소의 `CLAUDE.md`와 `project-context/STATUS.md`를 읽고 현재 단계·사용자 역할·Claude 검수 역할을 이해했음을 확인했다. 해당 검수에서 파일은 수정하지 않았다고 보고했다.
- 지침 배치 전 저장소에는 `README.md`, `.vscode/`, `.git/`가 있었다. 런타임 구현 코드는 아직 확인되지 않았다.
- 기존 `.vscode/`는 미추적 상태였고 이번 작업에서 수정하지 않는다.

## 이번에 준비한 내용

- 루트 `AGENTS.md`, `CLAUDE.md`에 두 도구의 역할과 참고 문서 안내를 작성했다.
- `project-context/`에 원문 두 개와 런타임 참고 요구사항, 현재 상태를 배치했다.
- 앞선 준비 작업에서 `README.md`의 기존 내용을 유지하면서 당시의 개발계획서 작성 중·구현 범위 미확정 상태와 Codex 구현·수정·시험 / Claude 검수 역할을 추가했다. 해당 README 작업의 Git 기준점은 `51be68f`이며, 그 작업에서 커밋·푸시는 하지 않았다.
- 2026-10-19 사용자가 전달한 Claude 검수 결과에 따르면 이번 README 변경의 검수가 완료됐으며 필수 수정 사항은 없다. 기존 문서와 충돌이 없고, 환경 확인 기록의 근거도 충분하다는 판단을 전달받았다.
- 2026-10-19 개발계획서 v1.0 초안을 읽고 `CLAUDE.md`에 필요한 상황에서 관련 절을 참고하도록 안내를 추가했다. 이 상태 기록의 미전달 내용도 갱신했으며 계획서 원문은 수정하지 않았다. 이번 문서 연결 작업의 Git 기준점은 `1cf7bba`이며, 코드 구현·도구 설치·커밋·푸시는 하지 않았다.
- 2026-10-19 사용자가 전달한 Claude 지적을 계획서 v1.0 §1.1.1·§1.8·§1.11(v1.4 §1.1·§8·§11)과 기존 문서에 대조해 문서별 역할·충돌 처리, 이전 요구와 새 초안의 범위 차이, 초안 전달·남은 결정 상태를 정리했다. 변경 파일은 `CLAUDE.md`, `runtime.md`, `README.md`, `AGENTS.md`, 이 상태 기록이다. 이전 요구사항 본문과 계획서 원본·사본은 보존했다. Git 기준점은 `1cf7bba`이며, 새 파일·코드 생성·도구 설치·커밋·푸시는 하지 않았다.
- 2026-10-19 사용자 요청에 따라 Python 최소 프로젝트 틀을 작성했다. 새 파일은 `.gitignore`, `pyproject.toml`, `src/serving_runtime/{__init__,__main__,cli}.py`, `tests/test_cli.py`다. `README.md`에 준비·실행·시험 명령을 추가하고 `CLAUDE.md`에 Python 선택을 반영했다.
- 개발·시험 환경은 Python 3.12.3 / WSL Ubuntu 24.04다. Codex가 최소 개발 도구로 프로젝트 전용 `.venv`·pip, setuptools 빌드 백엔드, 표준 `unittest`를 선택했다. 실행 라이브러리 의존성은 없다. 다른 Python 버전·OS는 미검증이다.
- `.venv/bin/python -m pip install --disable-pip-version-check -e /home/bobgy/bobproject/serving-runtime` 설치 성공. `.venv/bin/python -B -m unittest discover -s tests -v`는 설치된 패키지의 도움말·버전·알 수 없는 옵션 거부 시험 3개 통과. `.venv/bin/python -m pip check`와 추적 파일 변경에 대한 `git diff --check`도 종료 코드 0으로 통과했다. 새 파일 6개는 별도 Python 검사로 줄 끝 공백·최종 개행과 Python 구문을 확인했다.
- Python 최소 틀 검증 당시 실행 기능은 도움말과 버전 조회뿐이었다. Gateway·센서·저장·웹 서버·공통 스키마·보안 통제 기능은 미구현이며, 엔진·실제 통제 환경 시험은 미실시다. 개발 버전 `0.0.0.dev0`는 기능 완료를 뜻하지 않는다.
- Python 최소 틀 작업의 Git 기준점은 `b765a74`다. 계획서 SHA-256은 기존 기록과 일치하며, `.vscode/`와 시스템 Python 설정은 수정하지 않았다. 당시 검수·개선 작업에서는 커밋·푸시하지 않았다. 사용자가 전달한 Claude 코드 검수 결과는 필수 수정 없음이며, 선택적 개선 4건을 실제 파일에 대조해 반영했다. 알 수 없는 시험 옵션의 이름·표현, 인계용 README 경로, AGENTS의 Python 선택, 검사 범위 기록을 정리했다. 개선 후 실행 시험 3개를 다시 통과했고, 추적 파일 공백 검사와 새 파일 6개의 구문·공백 검사도 통과했다.


- 2026-10-19 사용자 요청으로 `file-info PATH`를 추가했다(계획서 v1.0 §1.6.5(v1.4 §6.5)의 크기·해시 확인에 활용하는 독립 개발 도구). 지정한 일반 파일을 나눠 읽고 읽은 바이트 수·SHA-256을 표시한다. 파일 내용·권한을 쓰지 않으며, 공통 스키마·승인 판정·변조 방지·모델 로딩 확인 기능은 아니다.
- 없는 파일·디렉터리·읽기 실패·관측된 읽기 중 변경은 성공 결과 없이 오류로 처리한다. FIFO는 기다리지 않고 거부한다. 새 파일은 `src/serving_runtime/file_info.py`, `tests/test_file_info.py`; 수정 파일은 CLI·CLI 시험·README·이 상태 기록이다.
- `.venv/bin/python -B -m unittest discover -s tests -v`로 기존 3개와 새 시험 9개, 총 12개가 통과했다. 알려진 SHA-256·빈 파일·큰 바이너리·원문 보존·FIFO·읽기 중 변경·CLI 정상/오류 경로를 확인했다. 추적 파일 `git diff --check`와 새 Python 파일 2개의 구문·공백 검사도 통과했다. 엔진·실제 통제 환경 시험은 미실시다.
- 이번 기능의 Git 기준점은 점검 시 HEAD인 `26da771`이다. 새 실행 의존성·도구 설치·커밋·푸시는 없으며, `.vscode/`와 계획서 원문은 수정하지 않았다. 2026-10-21 Claude가 이후 변경인 `cli.py`, `file_info.py`, `test_cli.py`, `test_file_info.py`, README 관련 절을 직접 검수했으며 필수 수정과 코드 수정은 없다.
- Codex 샌드박스에서는 `/tmp` 쓰기 제한으로 기본 시험 20개가 모두 임시 디렉터리 오류였으나, `TMPDIR=/dev/shm`으로 재실행해 20개 전부 통과했다(`test_file_info` 5개, `test_cli` 7개 포함). `file-info` 수동 실행은 README·없는 경로·디렉터리에서 각각 종료 코드 0·1·1로 기대대로 동작했고 `git diff --check`도 통과했다. 엔진·실제 통제 환경 시험은 여전히 미실시다.
- 이번에 반영하지 않은 선택 사항은 README "실행·시험" 절의 `file-info` 시험 설명(README 해당 절 삭제로 더 이상 해당하지 않음), `-`로 시작하는 경로에 필요한 `--`, 문자 장치 등 특수 파일을 거부하기 전에 `open`이 일어나는 점이다.
- 2026-10-21 협업 문서의 기본 흐름을 사용자 요청 정리(Claude) → 구현·시험(Codex) → 대화형 직접 검수(Claude) → 필요 시 수정·재시험(Codex) → 최종 보고(Claude)로 통일했다. `scripts/claude_review.py`와 해당 시험은 로컬 미커밋 상태로 보존하며 자동 실행하지 않는다.
- 2026-10-21 원격 커밋 `06b7112`("Update README.md", 2026-10-19 사용자 작성)가 README의 "Python 프로젝트 틀" 절을 삭제했고 사용자 결정으로 이를 유지했다. 그래서 README에는 준비·실행·시험·`file-info` 사용법이 없다. 로컬 커밋 2개를 그 위로 rebase해 `b026fe0`(`file-info`), `f65608d`(협업 문서)로 커밋·푸시했다. `scripts/claude_review.py`, `tests/test_claude_review.py`, `.gitignore`의 `.review/`, `.vscode/`는 검수 전 또는 개인 환경 파일이라 커밋하지 않았다.
- 2026-10-21 로컬 전용 MVP 계획서(커밋 대상 제외)를 기준으로 M0+M1을 구현했다. 새 파일은 `src/serving_runtime/mvp/` 6개 모듈, `examples/mvp/policy.json`, `tests/test_mvp_{policy,validator,mock_engine,app}.py`이며 CLI에 `mvp`를 연결했다.
- Claude가 소켓 허용 환경에서 전체 43개 통과(건너뜀 0)와 serving-runtime mvp 실제 기동·/api/status 200·POST 501을 확인했다. Codex 샌드박스에서는 소켓 제한으로 HTTP 7개가 건너뛰어졌다.
- 2026-10-21 Claude 필수 지적에 따라 메시지 `role`의 문자열 형식을 집합 조회 전에 검사하고, 요청·정책의 깊은 중첩 JSON `RecursionError`를 `MALFORMED_JSON`으로 처리했다. 지정한 validator·policy 회귀시험 17개가 모두 통과했다.
- 2026-10-21 M2 Gateway 중계·서버 발급 요청 ID·Gateway/모의 엔진 JSONL 기록·전달 확인·저장/엔진 오류 처리를 구현했다. 새 파일은 `src/serving_runtime/mvp/store.py`, `tests/test_mvp_{gateway,store}.py`이며 config/app/mock_engine/CLI와 기존 MVP app 시험을 갱신했다.
- `.venv/bin/python -B -m unittest discover -s tests -v` 결과 60개 중 42개 통과, 소켓 제한으로 HTTP 18개 건너뜀이다. 기본 데이터 위치는 `$XDG_STATE_HOME/serving-runtime/mvp` 또는 `~/.local/state/serving-runtime/mvp`다. 이후 Claude가 소켓 허용 환경에서 60개 전부 통과(건너뜀 0)와 실제 기동 요청(200 중계·403 미전달·프롬프트 미저장)을 확인했다.
- 2026-10-21 M3를 시작한 Codex 작업이 사용 한도로 중단돼(`query.py` 초안·`app.py` 일부 변경, 시험 2개 실패 상태) 사용자 승인으로 이번 단계만 Claude가 직접 구현했다. 조회 API(`/api/summary`·`/api/requests`·상세·증거 내려받기), 읽기 전용 대시보드(`mvp/static/` 현황·목록·상세, textContent 렌더링, 3초 갱신·연결 끊김 표시), 정책 무효 시 1 MiB 본문 상한, 시작 시 저장 경로 점검, 엔진 수신 기록 저장 상태 표시를 추가했다. 이전 관측 세션 요청은 실시간 조회의 미발견을 미수신 증거로 쓰지 않는다.
- `.venv/bin/python -B -m unittest discover -s tests` 결과 76개 전부 통과(Claude 실행, 소켓 허용 환경). M3 코드는 Claude가 구현했으므로 Codex 교차 검수가 필요하다. 사용자가 Windows 브라우저에서 대시보드 접속을 확인했다. 사용자 요청으로 M0~M3 코드·시험과 README MVP 실행 안내를 커밋·푸시했다(로컬 전용 MVP 계획서, `scripts/`, `tests/test_claude_review.py`, `.gitignore` 변경, `.vscode/`는 제외). M4는 미완료다.

## 다음 준비 작업

- 파일 정보 확인 기능의 구현·시험·Claude 대화형 검수는 완료됐다. 다음 작업은 사용자 요청을 기다린다.

## 갱신 방법

확인한 날짜·변경·실행 명령·실제 결과·증거 위치·미확인·다음 작업을 짧게 유지한다.
계획과 실행 결과를 구분하고, 오래된 다음 작업은 교체한다. 긴 로그는 원본 경로만 남긴다.
