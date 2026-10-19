# 런타임 작업 상태

최종 갱신: 2026-10-19

## 현재 단계

- 2026-10-19 사용자 신가영이 작성한 개발계획서 v1.0 초안이 전달됐다. 저장소 사본은 `project-context/런타임_통제_개발계획서_v1.0.md`다.
- 사용자 요청으로 직접 개발할 모듈의 언어는 Python으로 선택했다. 실제 환경·연동 계약·정책값 등 남은 결정은 별도 확인이 필요하다.
- 초안 전달은 기술 스택 확정·팀 승인·구현 완료를 의미하지 않는다.
- 현재 요청 범위는 외부 연동 없는 Python 최소 프로젝트 틀의 구현·설치·기본 실행·시험이다. 보안 기능의 구현·집행 검증은 포함하지 않는다.
- Codex는 구현·수정·시험, Claude는 검수를 기본 역할로 맡는다.

## 확인한 환경

- GitHub 저장소: https://github.com/yeong-yi/serving-runtime
- WSL 저장소 경로: `/home/bobgy/bobproject/serving-runtime`
- 2026-10-19 Codex에서 `pwd`, `git rev-parse --show-toplevel`, `uname -a`와 WSL 환경 변수를 확인했다. 작업 폴더와 Git 루트가 위 경로로 일치하며, 명령은 WSL2의 Ubuntu 24.04에서 실행된다.
- 원본 맥락 문서 위치: `/home/bobgy/bobproject/project`
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
- 2026-10-19 사용자가 전달한 Claude 지적을 계획서 §1.1.1·§1.8·§1.11과 기존 문서에 대조해 문서별 역할·충돌 처리, 이전 요구와 새 초안의 범위 차이, 초안 전달·남은 결정 상태를 정리했다. 변경 파일은 `CLAUDE.md`, `runtime.md`, `README.md`, `AGENTS.md`, 이 상태 기록이다. 이전 요구사항 본문과 계획서 원본·사본은 보존했다. Git 기준점은 `1cf7bba`이며, 새 파일·코드 생성·도구 설치·커밋·푸시는 하지 않았다.
- 2026-10-19 사용자 요청에 따라 Python 최소 프로젝트 틀을 작성했다. 새 파일은 `.gitignore`, `pyproject.toml`, `src/serving_runtime/{__init__,__main__,cli}.py`, `tests/test_cli.py`다. `README.md`에 준비·실행·시험 명령을 추가하고 `CLAUDE.md`에 Python 선택을 반영했다.
- 개발·시험 환경은 Python 3.12.3 / WSL Ubuntu 24.04다. Codex가 최소 개발 도구로 프로젝트 전용 `.venv`·pip, setuptools 빌드 백엔드, 표준 `unittest`를 선택했다. 실행 라이브러리 의존성은 없다. 다른 Python 버전·OS는 미검증이다.
- `.venv/bin/python -m pip install --disable-pip-version-check -e /home/bobgy/bobproject/serving-runtime` 설치 성공. `.venv/bin/python -B -m unittest discover -s tests -v`는 설치된 패키지의 도움말·버전·알 수 없는 옵션 거부 시험 3개 통과. `.venv/bin/python -m pip check`와 추적 파일 변경에 대한 `git diff --check`도 종료 코드 0으로 통과했다. 새 파일 6개는 별도 Python 검사로 줄 끝 공백·최종 개행과 Python 구문을 확인했다.
- 현재 실행 기능은 도움말과 버전 조회뿐이다. Gateway·센서·저장·웹 서버·공통 스키마·보안 통제 기능은 미구현이며, 엔진·실제 통제 환경 시험은 미실시다. 개발 버전 `0.0.0.dev0`는 기능 완료를 뜻하지 않는다.
- 이번 구현의 Git 기준점은 `b765a74`다. 계획서 SHA-256은 기존 기록과 일치하며, `.vscode/`와 시스템 Python 설정은 수정하지 않았다. 이번 구현은 커밋·푸시하지 않았다. 사용자가 전달한 Claude 코드 검수 결과는 필수 수정 없음이며, 선택적 개선 4건을 실제 파일에 대조해 반영했다. 알 수 없는 시험 옵션의 이름·표현, 인계용 README 경로, AGENTS의 Python 선택, 검사 범위 기록을 정리했다. 개선 후 실행 시험 3개를 다시 통과했고, 추적 파일 공백 검사와 새 파일 6개의 구문·공백 검사도 통과했다.

## 다음 준비 작업

- 검수 및 선택적 개선·재시험이 끝난 Python 최소 틀을 저장한다. 이후 첫 실제 기능의 범위와 필요한 선행 결정만 확인한다.

## 갱신 방법

확인한 날짜·변경·실행 명령·실제 결과·증거 위치·미확인·다음 작업을 짧게 유지한다.
계획과 실행 결과를 구분하고, 오래된 다음 작업은 교체한다. 긴 로그는 원본 경로만 남긴다.
