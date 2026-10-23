# 자동화 2차 실행 프롬프트와 완료 계약

기준: `codex/gua-windows-automation-phase1` / `1d1463274529708ae5fe5e04f42c134ba5a10ec3`, 초안 PR16 미병합.
작업 브랜치: `codex/gua-automation-phase2`, PR base는 1차 브랜치다. 확인 후 최신 원격 1차와 로컬이 같았으며 main의 관계없는 변경은 합치지 않는다.

## 그대로 실행할 프롬프트

너는 gua 프로젝트의 자동화 2차 구현 담당이다. SPEC.md·CLAUDE.md·PROGRESS.md·DECISIONS.md와 1차 구현/실측 기록을 먼저 읽고 다음 묶음을 구현한다. 사용자는 다음 프롬프트 작성과 이어지는 구현·검증·문서화·커밋·푸시·초안 PR을 승인했다. 현재 요구사항을 벗어난 기업 정책값·새 의존성·GPU/유료 인프라는 추가하지 않는다. 과거 POLICY_PROMPT의 양식 전용/커밋 금지는 이번 승인 범위에 적용하지 않는다.

1. Windows CPU용 **명시적인 고정 chat template 시험 프로파일**을 추가한다. 현재 official pinned CPU vLLM0.31.0·opt125m snapshot을 그대로 쓰고, 패키지의 고정 무해 template를 모델 폴더 밖에 읽기 전용 마운트한다. 임의 파일 경로·임의 Jinja·동적 코드 실행 기능을 만들지 않는다. profile/version·바이트SHA·container path·실제 argv와 override 불허 조건을 비교 설정에 기록한다. 실행마다 달라지는 호스트 임시 절대 경로는 비교 해시에 넣지 않는다. 기본 completions 프로파일과 모델 inventory는 보존한다. chat endpoint의 기술적 활성화를 OPT의 지시 학습이나 system 비공개성 보장으로 설명하지 않는다.
2. **REQ-07 template 적용 후 입력 제한** 실행기를 구현한다. POL은 `allowed_template_sha256:list[str]`, `max_rendered_input_tokens:int`, `request_template_override_allowed:bool`의 명시적인 기준이다. SHA는 비어 있지 않은 중복 없는 소문자64hex 목록, 길이는 bool을 제외한 양의 정수, override는 정확한 bool이다. 기업 기준의 임의 상한을 만들지 않는다. 요청 수·시간·최대 probe/input/output token은 별도 실행 설정에 둔다. override 허용=true의 미지원 범위는 UNKNOWN으로 남긴다.
3. 고정 template 선택·정상 messages·실제 `/tokenize`의 token IDs·경계 안팎 chat 추론·실제 응답 `prompt_token_ids`를 대조한다. usage 개수가 같다는 사실만으로 실제 소비 일치를 선언하지 않는다. source에서 확인한 검사 위치와 오류/실제 소비를 구분한다. 짧은 원 입력이 template 후 커지는 시험을 포함한다. request template와 template kwargs의 고정 무해 override도 시험하되, 단순 200/400만으로 효과/거부를 단정하지 않는다. 미확인 소비·검사 위치·정상 대조는 UNKNOWN, 독립 확인 위반은 이후 수집 실패와 함께 있어도 FAIL을 보존한다.
4. 기존 **REQ-13 private_system 분기**를 새 chat 프로파일에서 실제 실행한다. 기존 1차 정책·FAIL·completions 관측은 변경하지 않는다. 별도 EXAMPLE/DRAFT 정책과 설정으로 선택 endpoint를 명확히 기록하고 전후 정상 대조와 private challenge attempted를 증명한다. 미지원 반환 옵션은 계속 UNKNOWN이다. 정책을 바꿔 결과를 PASS로 맞추지 않는다.
5. **CPU 사례 행렬의 시험 가정**을 고친다. 첫 두 구성의 노출5FAIL/강화5PASS 기대는 유지한다. 세 번째는 짧은 관측 시간 계약이며 UNKNOWN을 강제하지 않는다. PASS는 원본 EVD·고정 CheckDef·정상 대조·필수 관측·전체 챌린지·저장 RES/고정 판정표로 뒷받침돼야 한다. 자료 부족의 PASS나 독립 FAIL 지우기는 거부한다. 과거 14/15·행렬exit1·원본 EVD/RES/summary를 수정하지 않는다. 새 schema/version/새 출력에 관측 계약 결과를 기록하고, 기존 실제 행렬도 엔진 없이 읽기 전용으로 분석할 수 있게 한다. 결정적 미수집은 합성 계약 테스트로 별도 입증한다.
6. 새 점검 정의·고정 판정표·정책 양식 metadata·registry·수동 PASS 상한·JSON/HTML 보고서·예제를 연결한다. 팩 revision을 올리고 나머지76개 정의·기존 팩 snapshot·정책값·증거를 보존한다. 순수 정책 의미 검증을 실행기/판정기가 공유하되 판정기는 실행기·네트워크·시각·I/O에 의존하지 않는다. core/models/store와 객체7종·append-only·기존 bundle 포맷은 보존한다.
7. **구현·테스트 코드·문서 초안을 모두 작성한 후에** 기본 전체 테스트와 필요한 실제 엔진 시험을 실행한다. 그 뒤 실패 원인을 구현/fixture/시험 가정/환경으로 구분해 수정하고 필요한 재검증을 한다. 기존 테스트·불변식·확인된 FAIL·필수 관측을 완화하지 않는다. 실제 엔진은 한 번에 하나만 띄우고 기존 kind 등 다른 컨테이너는 중지·삭제하지 않는다. 모델 원본과 과거 실행 증거를 덮어쓰지 않는다.
8. 직접 실행과 과거 기록, 합성·stub·실제 엔진을 분리해 보고한다. Windows CPU 성능은 UNKNOWN, 보고서는 EXAMPLE/DRAFT·official=false다. 단일 이미지 실행을 두 버전 업데이트 비교나 GPU/기업 시스템 검증으로 세지 않는다. 최종 frozen 설치·wheel 외부 설치본·반출/복원/저장 증거 재판정·metadata/팩 재생성·파일 보존을 확인한 뒤 커밋·푸시·초안 PR을 만들고 최종 SHA의 CI를 확인한다. 병합이나 force push는 하지 않는다.

## 완료를 입증할 산출물

실제 CPU 첫 생성이 기존 30초 요청 관측을 넘으면 원래 UNKNOWN을 보존하고 원인을 기록한다. REQ-07의 유한 요청 timeout 상한은 120초, 새 요청 송신 마감은 300초로 구분하고 남은 시간으로 제한한다. 새 설정의 별도 실행을 사용하며 retry·정상 대조 교체·기업 정책/판정 완화는 하지 않는다. 다른 실행기의 timeout은 이 조정에 포함하지 않는다.

- 고정 chat profile·바이트SHA·readonly mount·안정된 비교 hash와 기존 launch 보존 회귀.
- REQ-07 정책3경로·bounded 실행기·고정 판정표·수동 의미 상한·정상/위반/부분 관측/같은 개수 다른 token IDs/후속 실패의 회귀.
- 실제 chat/tokenize/consumer token 대조와 REQ-13 private_system attempted, 두 점검의 정상 대조·actual 결과·보고서·Ref·무결성 기록.
- 과거 14/15 원본을 바꾸지 않는 행렬 분석 결과, 충분한 관측 PASS와 불충분한 관측 UNKNOWN을 구분하는 계약 회귀.
- 새 빈 정책 양식·DRAFT 예제·9개 자동 연결 상태·새 팩/metadata 재생성 일치·원문/과거 증거/모델/컨테이너 보존.
- PROGRESS/DECISIONS/검증 문서·최종 기본 테스트/설치/실제 기록·커밋·원격 동일 SHA·초안 PR·CI 결과.

## 이번 묶음 이후

REQ-11 일반/SSE 반환 귀속은 별도의 bounded streaming 수집기와 실제 연결별 원인/결과 기록을 확보한 다음 묶음이다. OBS-09는 실제 raw→parser→collector의 신뢰 필드 대응이 선행된다. REQ-10 하위 runner 소비와 OBS-03 실제 exception dump·기업 source 검증은 여전히 별도 과제다. 이 후보들을 이번 코드에 부분 구현하거나 완료로 계산하지 않는다.
