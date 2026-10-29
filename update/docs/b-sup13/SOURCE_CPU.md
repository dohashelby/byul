# SUP13 고정 template source CPU 경계

기존 guard-type-boundary-v1.jinja의 승인 bytes(38110f6e…abb9fe36)를 그대로 사용한다.
선정 source db9527a(v0.31.0), Python3.12, 기존 CPU 환경의 Jinja3.1.6에서 실제
safe_apply_chat_template → tokenizer.apply_chat_template를 정상→고정 무해 challenge→정상으로 호출한다.
기존 GPT2/WordLevel builder를 재사용하며 모델 가중치·네트워크·HTTP·GPU를 실행하지 않는다.

stdlib 단일 파일 guard.adapters.vllm_template_boundary.snapshot_template_boundary는
byul-template-boundary-snapshot/1을 반환한다. 선택 point는 safe_apply_chat_template,
resolve_chat_template, tokenizer.apply_chat_template 및 SandboxedEnvironment/
ImmutableSandboxedEnvironment.is_safe_attribute이다. 실제 code object의 CALL/RETURN/RAISE를
sys.settrace로 관측하고 원래 trace를 finally에서 복원한다. 실제 함수 본문을 바꾸지 않는다.
resolved template SHA, 고정 message case/hash, 실제 text/token 결과 count/hash,
__class__ 속성의 실제 RETURN bool과 integer_one 대상만 기술 요약으로 보존한다.
임의 객체 property/repr·generator를 실행하거나 입력/출력/예외 본문을 저장하지 않는다.
한 invocation SHA와 선택 부모 call SHA로 실제 호출 연결을 보존한다.

도구 API는 capture_template_cpu(source_root, workdir, *, source_profile='target031')이다.
component pin은 root가 소스 고정 뒤 명시 연결하며 None은 정식 capture를 요청 전에 닫는다.
source/전체 component·helper origin/bytes·builder bytes·fixture bytes를 확인한다.
최종 guard/보조 저장/후행 실행 실패는 이미 수집한 행과 실제 attribute 사실을 보존한다.
미수집 상태는 unavailable이며 exception/HTTP400/함수명만으로 속성 차단을 추론하지 않는다.
POL 금지 속성의 독립 위반 판정과 EVD 연결은 별도 producer가 담당한다. 전체 sandbox PASS,
Kubernetes serving 소비, worker 소비·자원 예산은 별도 조건이다. 기존 CPU image adapter gate는 유지한다.

직접 경계는 구현 고정 뒤 원문 source와 기존152 CPU 환경에서 1회 실행하며,
정식 CPU EVD는 root component pin을 연결한 통합 source에서 한 번 실행한다.

## 직접 경계 관측

고정 초안의 실제 경계 1회는 `/private/tmp/byul-sup13-boundary-20261029-1/summary.json`에
보존했다(11.908초). 정상 전후 실제 text RETURN은 80자/동일 SHA이며 challenge는
VLLMValidationError였다. 양 Jinja 메서드의 실제 __class__/integer_one 판단은 False였다.
첫 정상 호출의 template 컴파일 중에도 상수 접근을 예비 평가하므로 이 값만으로
challenge 실행의 차단을 확정하지 않는다. 최종 metadata는 실제 callstack에서
compile_or_optimize/template_runtime/unavailable을 구분하며 별도 실제 반복 없이
아래 영향 회귀와 root pin 이후 정식 capture에서 확인한다. 실제 Jinja file SHA와
각 메서드 code SHA/qualname 및 dependency_versions를 함께 보존한다.
관측 callback 오류는 원래 해석기의 반환/예외를 바꾸지 않고 observer_error_type으로 남긴다.
정식 CPU EVD는 아직 실행하지 않았다.

## 10월30일 관측 보완

case당 128개 기술적 이벤트 상한을 넘으면 앞의 snapshot을 보존하면서
observation_gaps=[event_limit_exceeded] 및 dropped_event_count를 남기고 전체 capture를
incomplete로 처리한다. 이미 확인한 별도 위반 관측은 유지한다.
선정 TokenizerPool.apply_chat_template의 실제 builtin args tuple/kwargs dict를 각 32개까지
정규화하여 CALL과 RETURN 모두 실제 conversation/template의 SHA를 수집한다.
정규화 시 임의 property/repr/iterator는 실행하지 않는다. tokenizer positional 입력은
실제 method self 다음의 conversation/tools/documents/chat_template 순서를 지원한다.

관측 보완 후 고정 code의 실제 경계는
`/private/tmp/byul-sup13-boundary-20261030-2/summary.json`에 보존했다(12.266초).
`wrapper-proof.json`에서 세 case 모두 실제 tokenizer CALL/RETURN의 template SHA와
messages SHA가 고정 fixture/해당 case와 일치함을 확인했다. 정상 전후 80자 반환 SHA는
동일하며 challenge는 VLLMValidationError, source post 검증 및 renderer 종료는 정상이다.
첫 정상 컴파일의 attr 판단은 compile_or_optimize, challenge의 판단은 template_runtime으로
각각 관측했다. 실제 경계에서는 overflow가 없고 overflow 실패 보존은 EXAMPLE 회귀로 검증했다.
정식 component 검증·EVD 수집은 root 최종 pin 이후 별도 한 번으로 남아 있다.

## 공통 명령 분석 재사용

`guard.adapters.template_boundary_evidence.analyze_cpu_capture(record, *, source_commit,
collector_sha256, callback_sha256)`는 기존 producer1080c66의 행/CPU 분석을 공유한다.
동일한 고정 template/메시지/정상·참조 반환 의미와 호출 트리·runtime 속성 판단을 유지한다.
호출자는 승인한 source commit 및 collector/native SHA를 반드시 명시하고 누락된 핀은
미수집으로 처리한다. 분석은 프로세스 실행·등록·팩 작성·POL 기본값을 만들지 않는다.
기존 `_analyze_row`/`_cpu_result`는 함수명·명시 인자와 로컬 SHA/date helper 참조만
기계적으로 바꿨고 정규화 AST SHA를 전용 회귀에 고정했다. shallow checkout에서도
과거 git history를 읽지 않고 동일성을 검사한다. caller가 실제 source/component/import
검증을 수행한 collector의 안전 record를 전달하는 기존 경계는 유지한다.

공통 executor의 child는 `python -m tools.b_sup13_template_cpu --source-root PATH --output OWNED`
형상을 사용한다. `--output`은 기존 `--workdir`와 같은 dest의 별칭이며 capture API와
실제 source 경계는 유지한다. 이전 임시 pack formal 준비 launcher는 실행하지 않는다.
정식 수집은 통합된 공통 executor/명령을 통해 한 번 실행한다.
