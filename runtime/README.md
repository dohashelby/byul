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

## 현재 상태 (2026-10-27)

| 구분 | 확인 수준 |
| --- | --- |
| 요청 통제 Gateway (chat·completions, 스트리밍, 인증, 출력·context 상한, 필드 제한) | 모의 엔진, **실제 vLLM v0.31.0 CPU** |
| 대상 등록·정책 묶음·승인 목록 | 모의·가짜, 실제 vLLM CPU, 로컬 kind(Pod 교체) |
| Cilium 통신 제한 · Hubble · Tetragon 관측/선택 차단 | 로컬 kind(WSL) |
| SGLang 연결 | 가짜 SGLang만 |
| AWS GPU lab 배포·인수 시험(AWS-01~08) | 배포 파일·자동 판정 도구 작성, **실제 AWS 미실행** |
| 시험 | unittest 482개, 수용 시험 39개, CPU vLLM 점검 CPU-01~14 통과 |

**아직 안 한 것:** 정식 GPU 환경 시험(S3B·AWS-01~08), 실제 SGLang, logprobs, 운영 인증 연동(D-02)·계정/역할(D-03). 기능별 상세 확인 수준은 [`docs/지원_미지원_목록.md`](docs/지원_미지원_목록.md)에 있습니다.

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
