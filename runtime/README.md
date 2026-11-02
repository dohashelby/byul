# serving-runtime

서빙엔진(vLLM·SGLang) 앞에서 추론 요청을 정책대로 허용·거부하고, 그 결과와 엔진·통신·파일 관측을 기록해 조회 API로 내보내는 런타임 통제 모듈입니다. 화면은 byul `platform/`이 맡습니다.

- 기준 문서: 개발계획서 v1.4(개발 기준 설계안). 연동 규격·정식 환경 등 남은 결정이 있습니다.
- 언어: Python 3.12, 표준 라이브러리만 사용(`dependencies = []`).
- 상세 설명·옵션·한계는 [`docs/상세_안내.md`](docs/상세_안내.md), 인수 담당 실행 순서는 [`docs/인계_안내.md`](docs/인계_안내.md)에 있습니다.

```text
클라이언트 → Gateway (인증·정책·context 상한 판정, 요청 ID 발급)
                │  허용만 중계
                ├→ 모의 엔진 (기본)  /  외부 vLLM  /  외부 SGLang(연결 준비)
                ↓
       요청 기록 · 엔진 상태/지표 · Hubble/Tetragon 관측 · 대상 추적
                ↓
       조회 API (GET /api/*) → byul platform 화면
```

## 현재 상태 (2026-11-02)

| 구분 | 확인 수준 |
| --- | --- |
| 요청 통제 Gateway (chat·completions, 스트리밍, 인증, 출력·context 상한, 필드 제한) | 모의 엔진, **실제 vLLM v0.31.0 CPU** |
| 대상 등록·정책 묶음·승인 목록 | 모의·가짜, 실제 vLLM CPU, 로컬 kind(Pod 교체) |
| Cilium 통신 제한 · Hubble · Tetragon 관측/선택 차단 | 로컬 kind(WSL) |
| SGLang 연결 | 가짜 SGLang만 |
| AWS GPU lab 배포·인수 시험(AWS-01~08) | AWS GPU dev에서 1회 통과. 정식(reference) 시험·속도 측정은 미실행이며, 기능 완성을 뜻하지 않음 |
| 시험 | unittest 482개, 수용 시험 39개, CPU vLLM 점검 CPU-01~14 통과 |

**아직 안 한 것:** 정식 GPU 환경 시험(S3B·reference AWS-01~08)과 속도 측정, 실제 SGLang, logprobs, 운영 인증 연동(D-02)·계정/역할(D-03). 기능별 상세 확인 수준은 [`docs/지원_미지원_목록.md`](docs/지원_미지원_목록.md)에 있습니다.

## AWS dev 시험에서 겪은 문제

### 코드·설정을 고친 문제

| 문제 | 조치 |
| --- | --- |
| 엔진 요청 로그가 꺼져 AWS-01~03이 `unknown` | 통합 patch에 `--enable-log-requests`, `--enable-request-id-headers` 추가 |
| 실제 intent를 묶음에 못 넣음(묶음 검사는 `schema_version`, intent는 `schema`; 가짜 파일 시험으로 못 잡음) | `policy_bundle.py` 수정, 실제 형식 시험 추가 |
| 죽은 Pod까지 세어 재시작 뒤 `multiple_targets` 503 | `target.py`와 시험 도구 수정 |
| Gateway 응답 대기 5초로 상한값 요청을 504로 오판할 위험 | 60초로 조정 |
| GPU 이미지 실행 파일이 `/usr/bin/python3.12` | intent `phase_binaries`에 추가, 단계별 판정은 미확인 |

### 시험 전 검토에서 고친 판정

| 문제 | 조치 |
| --- | --- |
| AWS-03이 Gateway 기록만 믿음 | 엔진 로그와 상한값 대조 추가 |
| AWS-04·05가 연결 실패 이유를 안 봄 | 시간 초과와 Hubble `dropped`만 인정 |
| AWS-06이 권한 오류로도 통과 | `EROFS`만 인정 |
| AWS-07이 회복 뒤 실제 요청을 안 봄 | 정상 요청 200 필수 |
| AWS-08이 재시작 직후 1번만 요청 | 엔진 정보 회복 뒤 정해진 시간 안에서 재시도 |
| AWS-01이 stream을 안 봄 | stream 요청 추가 |

### 설치 중 겪은 문제

| 문제 | 해결법 | 후속 |
| --- | --- | --- |
| `01-host.sh`의 `sudo -v`가 이 Ubuntu 이미지에서 실패 | AWS 묶음 원본 대신 서버 사본에서 그 줄만 삭제하고 차이 보관 | 묶음 담당자에게 공유 필요 |
| 설치 중 자동 업데이트(`unattended-upgrades`)가 패키지 잠금을 잡음 | 끝난 뒤 이번 실행이 만든 `config.toml`을 옮기고 서버 사본에서 재실행 | 묶음 담당자에게 공유 필요 |
| 서버 재시작 때 GPU 플러그인이 늦어 엔진 Pod 하나 실패 | 자동으로 새 Pod가 뜸, 실패 Pod는 제외하도록 코드 수정 | 문서에 절차 추가 |
| 후보 규칙 `runtime-receiver-fixture-ingress`가 AWS-05와 충돌 | 적용에서 제외 | 문서에 기록 |

### 실행 중 실수

| 실수 | 영향 | 조치 |
| --- | --- | --- |
| `pkill` 패턴이 원격 셸까지 끊어 증거 복사 실패, 그대로 Stop | 보고서를 다음 실행 때까지 못 가져옴 | 다음 실행 때 회수, 파일 확인 후 Stop으로 절차 변경 |
| 복사 명령 출력이 복사본에 섞임 | 복사본 생성 실패 | 다시 복사하고 건수 확인 |
| 기본 정책 자동 복원이 경로 문제로 실패 | 기본 정책이 약 1분 빠짐, runtime 규칙은 적용 상태 | 바로 수동 복원, 원본과 일치 확인 |
| 서버 켜기 전 엔진 로그 옵션 대조 안 함 | 켠 뒤 발견해 수정 때문에 Stop | 수정 후 재실행 |

상세 근거: [`deploy/gpu-lab/ACCEPTANCE.md`](deploy/gpu-lab/ACCEPTANCE.md).

## 빠른 시작

`runtime/` 폴더에서 실행합니다.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .

# 시험
.venv/bin/python -B -m unittest discover -s tests
.venv/bin/python -B -m serving_runtime mvp-acceptance

# 모의 엔진으로 Gateway 실행 (:8080, 모의 엔진 :8088)
.venv/bin/python -m serving_runtime mvp
```

화면은 byul 루트에서 `platform/scripts/run-with-runtime.sh`로 runtime(모의 엔진)과 platform(:8700)을 함께 띄웁니다. 다른 Gateway를 보려면 platform을 `RUNTIME_BASE_URL=<Gateway 주소>`로 실행하고, 조회 보호를 켰다면 `RUNTIME_VIEWER_TOKEN`도 넘깁니다. platform은 runtime 주소를 하나만 받습니다.

## 실행 환경별 안내

| 환경 | 위치 |
| --- | --- |
| 실제 vLLM v0.31.0 (CPU, 이 PC) 점검 | [`deploy/cpu-lab/README.md`](deploy/cpu-lab/README.md) · `tools/cpu_vllm_check.py` |
| 로컬 kind lab (Cilium·Hubble·Tetragon) | [`docs/상세_안내.md`](docs/상세_안내.md) "통신·행위 통제 준비" · `tools/k8s_lab_check.py` |
| AWS GPU lab 배포·인수 시험 | [`deploy/gpu-lab/README.md`](deploy/gpu-lab/README.md) · [`deploy/gpu-lab/ACCEPTANCE.md`](deploy/gpu-lab/ACCEPTANCE.md) · `tools/aws_lab_acceptance.py` |

## 문서

| 문서 | 내용 |
| --- | --- |
| [`docs/상세_안내.md`](docs/상세_안내.md) | 기능별 설명·CLI 옵션·응답 코드·기록 형식·한계 (이전 README 전체) |
| [`docs/인계_안내.md`](docs/인계_안내.md) | 인수 담당 직접 실행 순서·기본 복원·체크리스트 |
| [`docs/지원_미지원_목록.md`](docs/지원_미지원_목록.md) | 기능·엔진별 확인 수준 |
| [`docs/인수시험_대응표.md`](docs/인수시험_대응표.md) | 계획서 시험 항목과 현재 근거 연결 |
| [`docs/runtime_규칙목록.md`](docs/runtime_규칙목록.md) · [`docs/내부_기록형식.md`](docs/내부_기록형식.md) | 사유 코드·탐지 규칙, 기록 형식 |
| `docs/결정기록_*.md` | 인증 연동, 엔진 UID, 정책 묶음 수신 결정 |
| [`project-context/STATUS.md`](project-context/STATUS.md) | 작업 이력과 미확인 사항 |

## 협업·검수

사용자 요청을 Claude가 정리하고 Codex가 구현·시험한 뒤, Claude가 검수해 최종 보고합니다. Codex 사용 한도 중에 Claude가 구현한 부분은 STATUS에 "Codex 교차 검수 전"으로 표시합니다.
