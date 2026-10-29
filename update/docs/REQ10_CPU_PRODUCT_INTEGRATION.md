# B REQ10의 기존 CPU 제품 연결

B 확정 `57a7537`의18커밋/24파일 bytes를 보존한다. 기존 `guard` CLI와 전용
`StructuredToolsCPUExecutor`, `StructuredToolsTarget031CPUExecutor`에 아래 두 등록만
추가한다. PACK31의 numeric/Kubernetes sampling 경로와 의미는 유지한다.

- `[executors] structured_tools_cpu="reference-cpu"`와 형제 `[structured_tools_cpu]`
- `[executors] structured_tools_target031_cpu="source031"`와 형제 `[structured_tools_target031_cpu]`

설정 필드는 B 인계 그대로 `source_root`, `model`이다. 새 CLI/가짜 실행기나 capture
JSON import가 없다. `tools/build_req10_cpu_inputs.py`에 profile/reference 또는 vllm-031,
origin/EXAMPLE 또는 DECLARED, model과 새 output을 명시하면 B 기존 CPU proposal과 같은
정확 팩 Ref·ENV/POL을 준비한다. 관측·실행·정책 승인은 하지 않으며 MEASURED 입력은
생성하지 않는다. 생성한 `proposal-pack`, `environment.json`, `policy.json`과 설정을
기존 select/run/judge/report/verify 및 evidence export/restore에서 사용한다.

참조5d35와0.31 source db952 프로파일·환경·의존성을 구분한다. B의 live producer가
전체 source/import/dependency를 실행 전후 검사한다. origin은 DECLARED 상한이며
EXAMPLE을 유지한다.0.31은 전용 reference ENV와 model 일치도 검사한다. 서버 전처리,
runner·GPU·실제 모델 세대·서비스 생존은 이 CPU 변환 경계의 결과로 대신하지 않는다.

소스 gate는 검토한 공통 통합 커밋의 전체 기존 범위·archive bytes·index/dirty·shadow를
같은 방식으로 검사하는 명시 소비 pin으로 연결한다. 실행 중 읽은 해시를 승인하거나
공통 경로를 제외하지 않는다. B 원본의 c9 기본 pin은 보존하며 A1이 제공한 고정 소비
커밋/해시 인계를 B가 받아 기본 도구를 옮기는 단계는 별도다.

CPU 주입 사례와 설치본 CLI의 정확 Ref 왕복은 제품 연결 회귀다. 기존 B 실제 CPU
EVD는 당시 source/environment Ref로 보존하고 새 통합 소스의 live 실행으로 소급하지
않는다. 현재 이 Windows 환경에 전체 고정 vLLM CPU source·동일 의존성이 없으면 그
live 단계의 미완료와 재개 조건을 기록한다. 선택한 배포 대상의 실제 관측은 별도로 필요하다.
