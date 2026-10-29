# SUP-13 고정 template의 source CPU 분기

원 PACK-BASELINE revision31의 SUP-13 질문·필수 정책·관측·rule_table을 그대로 사용한다. 선정 범위는 고정 공급 template `guard-type-boundary-v1.jinja`의 정상→무해 `__class__` 참조→정상 렌더다. 실제 db9527a(vLLM0.31.0) `safe_apply_chat_template`와 tokenizer를 기존152패키지 CPU 환경에서 호출한다. 모델 가중치·HTTP·GPU를 실행하지 않는다.

## 공통 실행 연결

`TemplateSourceCPUExecutor`는 공통 `guard run`의 기존 executor protocol로 실제 collector child를 한 번 호출한다. 원 CheckDef에 EvidenceDraft를 반환하고 기존 judge/report/Store를 사용한다. 새로운 임시 팩과 전용 EVD producer는 이 최종 변경에 포함하지 않았다. 기존 시제품은 별도 peer commit1080c66에 보존하며 실행하지 않는다.

A 단일 작성의 registry 연결은 아래 두 줄이다. [작성 조율 요청](https://github.com/dohashelby/byul/pull/67#issuecomment-6501784050)의 확정 소스에서 공통 CLI 실증1회를 수행한다. 현재 등록은 미반영이며 실증 EVD 수집은0회다.

```python
from .template_source_cpu import TemplateSourceCPUExecutor
"template_validation:source-cpu": lambda cfg: TemplateSourceCPUExecutor(cfg.get("template_validation", {})),
```

```toml
[executors]
template_validation = "source-cpu"
[template_validation]
source_root = "/approved/vllm-db9527a"
python_binary = "/approved/existing-cpu-env/bin/python"
producer_checkout = "/approved/byul-sup13-checkout"
collector_sha256 = "f82e848cc3388cbd32a4716cd1c05b17f0b978f8a2987ebf8c3aafef50bd6202"
callback_sha256 = "e41aac44f75370f08301887fcb951548e848bfcf67340de4a05c2fe0eca034c4"
```

```sh
guard select --pack /approved/pack31 --env /owned/ENV.json --policy /owned/POL.json --config /owned/guard.toml --store /owned/new-store
guard run --plan PLAN_ID --config /owned/guard.toml --store /owned/new-store
guard judge --plan PLAN_ID --config /owned/guard.toml --store /owned/new-store
guard report --plan PLAN_ID --config /owned/guard.toml --store /owned/new-store --json /owned/RPT.json
guard verify --store /owned/new-store
```

ENV는 source-db9527a46873454610df6dbedf79a36d6bf1a7f6이며 model/image를 지정하지 않은 별도 CPU ENV다. POL은 원 정의의 필수 forbidden_template_attributes를 유지하고 미입력·None·명시[]를 구분한다. CPU 기술 대조의 DRAFT ['__class__']는 배포 정책으로 이월하지 않는다. 원 기능 probe는 미실행/UNKNOWN, 전체 sandbox 범위는 false이며 PASS 규칙은 추가하지 않는다.

## 최소 관측·판정

실제 고정 template SHA와 입력 hash, 같은 호출 트리의 Tokenizer/safe_apply 양쪽 실제 참조 반환을 위반 근거로 삼는다. 상수식이 컴파일 중 계산돼도 실제 참조 반환이 확인되면 보존한다. 속성 검사bool·예외·함수명·컴파일 사실만으로 위반/차단을 확정하지 않는다. Jinja compile/runtime 관측은 진단으로 보존한다. 추가 최종 runner·GPU/cache 내부 계측을 이 분기의 필수조건으로 확대하지 않는다.

정상 대조는 실제 전후 결과로 True/False/None을 구분한다. 후속 child/보조 쓰기/첨부 실패는 독립 위반을 지우지 않는다. 128이벤트 상한 초과는 누락 수와incomplete를 기록한다. 원문 입력·반환·token·예외 문장은 EVD 첨부에 남기지 않는다.

자식은 승인 collector/native bytes를 전후 확인하고 기존 helper의 source/import/archive/clean 검사를 유지한다. 소유 새 capture/cache/TMP와 offline CPU·빈plugins를 사용하고 고정 bounded 자식만 회수한다. source component는 8d33d7ec7fa43eb23a3a7c1cd4be20131c8b4ca1다. 소스pin 변경은 legacy10도구의 검증 의미를 유지한 literal 갱신이며 AST 동일성 증거를 보존한다.

## 재사용한 직접 검증

- native/collector 영향회귀32개, 공유분석28개, 공통executor29개 통과. 중복 부분을 새 성과로 더하지 않는다. 집중검토의 관측누락2건·판정/환경/정상대조3건을 수정하고 fix-only 확인을 마쳤다.
- 실제 경계: 초안11.908초 원본과 wrapper 수정 후12.266초 원본을 각각 보존했다. 최종 후자는 정상전후80자/같은SHA, challenge VLLMValidationError, 실제 template/messages/hash와 source post 검증이 확인됐다. 이 자료를 최종 공통 CLI 실행으로 소급하지 않는다.
- source component 및 legacy product/fixture·collector 검증 통과. wheel162개 guard 파일 bytes 일치와 격리 설치 import, Torch/vLLM 미로딩을 확인했다. 기존 전체6514통과 결과는 기준으로 재사용하고 신규 module의 직접 영향 회귀와 제품 연결을 검증한다.

기존 추출 AST 동일성 증거와 실제 경계 원본은 영속 증거로 보존한다. 이미 통과한 source CPU 경계·전체 suite·다른OS를 반복하지 않으며, 최종 공통 CLI의 실제 collector1회·정확Ref·저장/반출/복원을 남긴다.

## 실제 환경 잔여

Kubernetes/GPU는 실제 실행0회다. 승인된 공급 template/모델·이미지/세대/설정·POL·run/복구 담당자와 대상이 아직 없다. 기존 CPU image adapter gate를 확대하지 않는다. 실제 배포 대상에서는 승인된 template와 기존 전송/수집 경로를 재사용해 필요한 분기만 검증하고 owned 계측/probe만 정리한다. Pod/공유GPU·서버를 기동하거나 중단하지 않는다. SUP-13 전체 완료로 집계하지 않는다.
