# REQ-10 CPU 도구의 공동 기준선 소비

`b1_integration_cpu.py`의 제품 기준선을 공동 커밋
`c9af729cd1893eda14d0bb222a8fab46c2a6d7b8`로 갱신했다.
PACK-BASELINE revision 31과 프레임워크
`91e84c778f4fc8ee1108eaf0551bf51f2f6a2061`을 명시적으로 확인한다.
기존 README의 main8761222는 당시 실행 이력이며, 이번 도구의 소비 기준은 이 문서다.

검사 범위는 `update/src/guard`, `update/packs`, `update/uv.lock`,
`update/pyproject.toml`, `update/reference/framework`이다. 기준 커밋의 ancestor 여부,
index와 작업 파일 차이, Git archive의 실제 파일 bytes와 작업 파일 bytes를 확인한다.
`assume-unchanged`로 diff에서 숨겨진 변경도 bytes 비교에서 거부한다.
미추적·ignored 파일도 검사하며 Python의 `__pycache__/*.pyc`만 실행 캐시로 허용한다.
legacy 위치의 `.pyc`, cache 디렉터리 안의 `.py`, 추가 import 소스는 거부한다.

vLLM은 기존 `5d35ce3934a7804b298559d2d99eec33792e1eb9` HEAD를 유지하고,
`vllm` 소스에도 동일한 snapshot 검사를 적용한다. 실제 요청 클래스와 예외 클래스의
import 위치, 재사용하는 제품 함수·클래스와 테스트 builder의 자기 worktree 위치를 확인한다.
실제 요청 클래스 결과와 EXAMPLE 추론 전송·제안 팩의 구분은 유지한다.

직접 검증은 자기 worktree `update`에서 형제 worktree의 Python 3.12 환경을 읽어 재사용했다.

```sh
PYTHONPATH=src:. ../../byul-b-c9af729/update/.venv/bin/python -m pytest \
  tests/test_b1_integration_pin.py tests/test_b1_integration_cpu.py -q
```

최종 결과는 18 passed, pytest 2.02초였다. 첫 성공 실행도 18 passed, 3.39초였다. pin 회귀 13개와 기존 EXAMPLE 제품 경로 5개다.
현재 실제 worktree의 기준선 통과, dirty/index-only/staged/missing/symlink 변경 거부,
미추적·ignored shadow 거부, 무관한 파일만 바꾼 후속 커밋 허용,
다른 root history 거부, `assume-unchanged` 변조 거부를 확인했다.
명령 준비 중 cwd와 상대 경로를 잘못 조합한 no-tests/실행 파일 없음 시도가 있었고, 경로 수정 후 위 시험을 실행했다.
실제 vLLM 클래스의 전체 `main` 실행은 이번 작업에서 수행하지 않았다.
제품 소스·팩·공용 문서·등록은 변경하지 않았다.
