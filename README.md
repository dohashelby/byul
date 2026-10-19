# serving-runtime
BoB serving-engine security runtime solution

- 개발계획서 v1.0 초안이 전달됐으며, 직접 개발할 모듈의 언어는 Python으로 선택했습니다. 연동 규격·실제 환경 등은 미결입니다.
- Codex는 구현·수정·시험을, Claude는 검수를 담당합니다.

## Python 프로젝트 틀

개발·시험 기준은 WSL Ubuntu 24.04의 Python 3.12.3입니다. 패키지의 최소 Python 버전은 3.12이며, 다른 버전·OS에서의 동작은 아직 검증하지 않았습니다.
`0.0.0.dev0`는 프로젝트 틀의 개발 버전이며 런타임 기능 출시나 구현 완료를 의미하지 않습니다.

현재 제공하는 것은 설치 가능한 패키지와 도움말·버전 조회입니다. Gateway·센서·저장소·웹 서버·정책 적용 기능은 포함하지 않습니다.
Python 실행 라이브러리 의존성은 없으며, 설치 시 패키지 빌드 도구인 setuptools가 필요합니다.

### 처음 준비하기

WSL 터미널에서 저장소 루트(`README.md`와 `pyproject.toml`이 있는 폴더)를 열고 실행합니다. 아래 설치 명령은 빌드 도구를 내려받기 위해 네트워크가 필요할 수 있습니다.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

프로젝트 전용 `.venv`를 사용하며, 가상환경을 활성화하지 않아도 아래 명령을 실행할 수 있습니다.

### 실행·시험

```bash
.venv/bin/python -m serving_runtime --help
.venv/bin/serving-runtime --version
.venv/bin/python -m unittest discover -s tests -v
```

시험은 설치된 패키지의 도움말, 실행 명령의 버전, 알 수 없는 옵션 거부를 확인합니다. 보안 통제·엔진 연동 시험은 아닙니다.
코드는 `src/serving_runtime/`, 시험은 `tests/`에 둡니다. 실제 인터페이스가 정해지면 필요한 기능만 추가합니다.
