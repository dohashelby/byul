# B 독립 GPU 실측 종료 기록 — 2026-11-04

REQ-05 단일 alias와 LIF-14 정상 profiler 분기를 실제 L4에서 실행하고, 앞선 PLT-01/04 독립 FAIL과 함께 검증·한계·정리 결과를 고정한다. 제품 실행기는 기존 registry factory를 사용했다. 신규 점검·판정 완화·자동 해시 승인·병합은 없다.

측정 소스는 `3bba3c73a6bee1966b784cd90a9690e3e9d2512a`이며 현재 main/PR head에 대한 인증 결과가 아니다. 관련 공통 코드 PR은 [#206](https://github.com/dohashelby/byul/pull/206)이고 이 변경은 문서와 공개 요약 JSON만 추가한다. 실제 ENV·POL·PLAN·EVD·RES·RPT와 저장 팩의 정확 Ref는 아래에 남긴다. 원자료는 별도 보관하며 공개 요약만으로 원 bundle을 재구성할 수는 없다.

## 고정 실행 조건

- 물리 NVIDIA L4 1개, TP1·PP1·노드1. 다중 GPU/TP2 실측이 아니다.
- 소스 tree `74ef2f1ee8e003f4bdb9c94205762b592b9acb07`, 검토 manifest `cffa0fe6ffe96b198ca3159238b8114098248ff5eb934f5ff432da8897c75d67`.
- 이미지 `vllm/vllm-openai@sha256:a4a4c0437bf7240089da5f08aa370c4aee17ae5290f7a3b468825ee26c4c3a6b`.
- 모델 `Qwen/Qwen2.5-0.5B-Instruct`@`7ae557604adf67be50417f59c2c2f167def9a775`, inventory `b52d654ef0bf6bf214e055f791b4e492206b9bd1380cb93d75bc4a092638245e`.
- PACK-BASELINE@32 SHA `1c4c723001eb3fcccb43e79c63e944663601e4d8b2939a84bff59730a8a3b8bd`와 실험용 DRAFT 정책을 유지했다. 원 모델·판정·센서 자산을 실험 중 교체하지 않았다.
- M06/M07은 서로 다른 namespace·engine/probe UID·CID·native15 session·target/prepared를 사용했다. 현재 boot·GPU·Node/Pod·GPU worker PID/start/UID/cgroup을 실행 전후 직접 결합했다. 상세 인프라 식별자는 공개하지 않는다.

## 제품 실행과 판정

| 실험 / 점검 | 실제 관측 | EVD | functional / performance / security |
|---|---|---|---|
| M05 PLT-01 | 정상 추론 성공, root·유효 capability14개·exec-writable 경로 관측 | COMPLETED / MEASURED | UNKNOWN / UNKNOWN / **FAIL** |
| M05 PLT-04 | 정상 추론 성공, 실험용 공개 비밀 아닌 forbidden marker 이름 관측 | COMPLETED / MEASURED | PASS / UNKNOWN / **FAIL** |
| M06 REQ-05 | allowed→denied→allowed = 200→401→200, 앞뒤 정상 응답 형식 확인 | COMPLETED / EXAMPLE | UNKNOWN / UNKNOWN / UNKNOWN |
| M07 LIF-14 | 기존 정상8단계 모두200, native 제어 API 연결4건, L4 trace2개 | COMPLETED / EXAMPLE | UNKNOWN / UNKNOWN / UNKNOWN |

PLT-01은 devices 미수집 때문에 기능 PASS를 보류했지만 확인된 보안 FAIL은 보존했다. PLT-04는 실제 관측된 일부 프로세스의 공개 시험 marker 이름에 대한 FAIL이며, 실제 클라우드 자격증명 유출·provider 사용·전체 worker/FD 범위의 증거가 아니다. M05의 RES data_origin은 DRAFT 정책 때문에 DECLARED이고 verification_level은 MEASURED다.

M06은 `model_authorization:kubernetes0.1.0` / `kubernetes-model-authorization-observation-v1`을 실제 실행했다. 세 요청의 binding·정책 시간창이 확인됐다. 세 요청 모두 최종 모델·주체/native request 연결은 미확인이며 `FINAL_MODEL_NATIVE_LINK_UNAVAILABLE`을 보존했다. 퇴역 revision의 명시적 빈 정책은 해당 분기 미선정·미실측으로 기록한다.

M07은 `profiler_observation:kubernetes0.1.0` / `kubernetes-profiler-control-v1`과 원 canonical9CLI를 실행했다. 네 제어 API의 native 연결은 HTTP status와 일치했고 session이 seal됐다. 정상 요청 네 개의 형식도 확인했다. 실제 단계는 다음과 같다.

| 단계 | HTTP | 정상 응답 형식 | 제어 응답 성공 |
|---|---:|---|---|
| normal-before | 200 | True | False |
| start | 200 | False | True |
| normal-active | 200 | True | False |
| stop | 200 | False | True |
| next-start | 200 | False | True |
| normal-next | 200 | True | False |
| next-stop | 200 | False | True |
| normal-after | 200 | True | False |

두 GZIP/JSON trace에서 L4 device 이름과 각각 CUDA kernel258개를 직접 확인했다. 이 파일 내용 관측은 제품의 source-authenticated backend 정지·출력 완료·API/worker RPC 인과·다음 backend 수집 검증과 구분한다. 해당 검증값은 false/UNKNOWN, activation 미입력도 UNKNOWN으로 유지한다. EVD의 정규 관측은 원 PACK32 activation4필드이며 원 profiler 관측은 SHA로 결합된 `profiler-control.json` artifact에 보존됐다.

| trace | bytes | CUDA kernel 이벤트 | SHA256 |
|---|---:|---:|---|
| 1 | 68709 | 258 | `7522a69baad778cb8866f4c41aec9fbdce259f245ca4f6e7f8c60c33eecd177c` |
| 2 | 69675 | 258 | `0bc93a1ec08317bad6f1ed639e89f7e96a6537d75ce2418d0c0bb1a8edd4910b` |

## 실패·미실측의 보존

| 기존 시도 | 실제 종료 경계 | 제품 실측 집계 |
|---|---|---|
| M01 | root Git 소유권 선행 오류 | factory 미호출 |
| M02 | 동일 UNIX socket 경로의 listener/accepted 소유 모호성 | 준비 실패, 제품 EVD 없음 |
| M03 | 소스 준비 중 mutable public branch head 변경 감지 | 엔진/factory 전 중단 |
| M04 | 운영자 target의 top-level environment_ref 누락 | EVD ABORTED / DECLARED, 유효 수집0 |
| M08 | UID1000·marker 없는 정상 대조의 기존120초 준비 기한 초과 | factory 전 실패 |

M08 실패를 소급 통과시키거나120초 기한을 늘리지 않았다. M06/M07의 기존 K3s 정상 준비600초, collector120/240초 및 원 CLI 종료 계약은 그대로다. 이 두 실행의 수집 abort는 없으며, 실제 GPU 실행이더라도 EXAMPLE을 MEASURED로 올리지 않았다. 기업 정책 미입력, 미지원 관측, 미실측 분기와 수집 실패를 같은 상태로 합치지 않는다.

## 증거 무결성과 정확 Ref

원격→CloudShell→데스크톱에서 ZIP CRC·모든 원자료 member bytes/SHA를 확인했다. 모든 정확 Ref의 bundle 복원과 저장 PACK32 재판정을 수행했고 같은 RES Ref를 확인했다. known-key 검사·원문 snapshot bytes→ZIP bytes 일치 검사와 child process group terminal 검증도 마쳤다. 원자료 ZIP·키·settings·원 로그·인프라 ID/로컬 경로는 공개하지 않는다.

| 원자료 | member 검증 | ZIP SHA256 |
|---|---:|---|
| M05 | 440 | `2e9f6ed0decd9eff701cea32cc61091b4e05778945395506bd898571066c5b77` |
| M06 | 425 | `a0edfb067a527485212a088bbdd8bf243d51b56543ce5597caa2d10eb6e7acc4` |
| M07 | 480 | `eac3d04cd398eab0b07fee5883c809c8655201fbdd00007f5c0e2cf037af35d9` |

전체 공개 요약과 Ref는 [동반 JSON](20261104-independent-measurements.json)에도 기록했다.

### M05 PLT-01 Ref

| kind | ID@revision | SHA256 |
|---|---|---|
| ENV | ENV-01M6GY5TG7HC3RCBCBRSEHNSZC@1 | `e08f04f035af7f3289a591ab5cc13882e895776305836def67f6b109a52d08dc` |
| POL | POL-01M6GY5TKKQM81SSA8QY42PWCT@1 | `ce7b7adaa1f49ef5dbf33196748eeb7ba39de45aad46aaf80c2af0348a73c824` |
| PACK | PACK-BASELINE@32 | `1c4c723001eb3fcccb43e79c63e944663601e4d8b2939a84bff59730a8a3b8bd` |
| PLAN | PLAN-01M6GY5TQEMJ6P78CW953NY4WF@1 | `0f8db6f44cf4972f0b1e1eff9707c28d044e1f9b513cc4a03103be50d836e826` |
| RPT | RPT-01M6GY5Y1X7T6817QCSTK553DV@1 | `07eb867f5b50dbba41c7610c7b1b48701d816a802841c23b4904e8231708a124` |
| EVD | EVD-01M6GY5TQY5Z670W17EDJF4E3G@1 | `48bb1e8740b17408eda1e51c9315d2df4cd4eb1e6cce6eb59ab004173222ab2a` |
| RES | RES-01M6GY5Y1H3M0PN48R15AX18JP@1 | `b78e523073d30446b9d18cd7f4fbf446cacbd951423e645cab64aa6cd8a0b49f` |

### M05 PLT-04 Ref

| kind | ID@revision | SHA256 |
|---|---|---|
| ENV | ENV-01M6GY5TG7HC3RCBCBRSEHNSZC@1 | `e08f04f035af7f3289a591ab5cc13882e895776305836def67f6b109a52d08dc` |
| POL | POL-01M6GY5YHGBB1SS7QCMZT3ACJ8@1 | `573a6348eb9bba8e7fedc2c336f2039b5906e832d3ccea93edfb7d0fcd3800e5` |
| PACK | PACK-BASELINE@32 | `1c4c723001eb3fcccb43e79c63e944663601e4d8b2939a84bff59730a8a3b8bd` |
| PLAN | PLAN-01M6GY5YMX3BBT3Y58HB5TSA9H@1 | `5b2a1dc7e9789136296056856dee098d51711a78d47015d688c1c787881fbc70` |
| RPT | RPT-01M6GY6053F4WPBEVD7FTWET4P@1 | `1bd10a422a7eaad862daf029b0df5e2623b8556e1c6fad4be7f0204ba37cc2ea` |
| EVD | EVD-01M6GY5YNBK9P3WC27M2WP9RHB@1 | `9dd993aaeb6cb4e32d0abb96fc7ea8834408c09ab3e821e185c455efe7ff5943` |
| RES | RES-01M6GY604PAFETB34SGX4WV8YZ@1 | `ccd559edbbb2f4f0cbca504ef52ce3fd0d75f6cc9a3422cf13ab46a091b7ab90` |

### M06 REQ-05 Ref

| kind | ID@revision | SHA256 |
|---|---|---|
| PACK | PACK-BASELINE@32 | `1c4c723001eb3fcccb43e79c63e944663601e4d8b2939a84bff59730a8a3b8bd` |
| ENV | ENV-01M6JC2YN9QBKSWQYVFX0EP0WC@1 | `ae638b57d2754cbc9efcb76a83db38ea486fd7e5feccc86763554e5c5dbf20ae` |
| POL | POL-01M6JC30GMT5KW4GEYK4SXKW0K@1 | `d1887c0c17bb35b34185f849ccc3e5ea059a3d20583dd9feb577c91da97bdbbb` |
| PLAN | PLAN-01M6JC30MFY8VKXCJR1V7VMKH0@1 | `82d85b0c56d7688a05e780f2a563472ad886440627e157f954e67e0d7d931b4e` |
| RPT | RPT-01M6JC3E9J5BC77HF90J42ZEMZ@1 | `5937d26485982e45cf07fa0dc2b0b759dc1230a760ab8c28f5794e59bc15248f` |
| EVD | EVD-01M6JC30MYNKWK1VBTPSX45S4S@1 | `de06ad3d4fa461d66d2dec8d23a90fabe41f13759f47fbf13b2fbbcf5ea3aa3d` |
| RES | RES-01M6JC3E97KXBXAHFW534DZDSD@1 | `df4590aefb16a46c520b60dc7166c02ab9eb998ee30e686f45484e6b9fdf6e48` |

### M07 LIF-14 Ref

| kind | ID@revision | SHA256 |
|---|---|---|
| PACK | PACK-BASELINE@32 | `1c4c723001eb3fcccb43e79c63e944663601e4d8b2939a84bff59730a8a3b8bd` |
| EVD | EVD-01M6JCMR644KNCZCVC6FC2FCER@1 | `2d8638c48b00627799947964ea60c84326cc6f89fd536122c304e324d4f59c8b` |
| RES | RES-01M6JCNZ8F1C707RTX27QB5HSW@1 | `7a5c044d787adb24566d8f91709ee8a2cadc2c32d160f46e322f8401f2aa4831` |
| RPT | RPT-01M6JCP0CJ7M3VZMJ7MA1A3X93@1 | `b874c792471100f3f622c3260a2cb9e79f242d30b40789d4581ba2f98958ab78` |
| ENV | ENV-01M6JCMJFPFPYQ2RS5ZVA4B0C1@1 | `b9917b7d8f3feae7958f430dce8d6eb2cc3cc53f356aca11797e5136a96ecdf6` |
| POL | POL-01M6JCMN3RS3K1T23V96W9GH7X@1 | `45b425f9ef62df2d71068599e4a21e1e524de35d66081314d43f763f2b86906d` |
| PLAN | PLAN-01M6JCMQ3AM7WE4SQXE10R7J5M@1 | `ec7d327eade7487600882ce1ab3d8afeda8ff560360b7301c4deaf6d9c2583a3` |

## 자원·비용·남은 범위

- 2026-11-04 04:38:33 UTC / 13:38:33 KST 실제 조회: 관련 EC2 세 대 모두 stopped, 공인IPv4 0, 진행 SSM0. 기존 EBS440GiB와 원자료·인스턴스 프로파일·보안 그룹을 보존했다. Force/삭제는 사용하지 않았다.
- 기존2h 비용 보호 타이머를 유지했다. 각 엔진은 정확한 소유 UID·현재 RV·label·replicas를 CAS 확인해 scale0했고 Pod와 GPU 프로세스 종료를 확인했다. 증거 반출·검증 뒤 EC2 정상 중지를 요청했고 위 실제 중지 관측까지 보수적으로 과금 구간에 포함했다.
- 위 시각의 두 세션 누적 보수 추정은 $38.32304662, 최소 $5 여유 제외 잔여는 $56.67695338다. EC2/EBS/IPv4와 관련 비용 충당 추정을 포함한다. CE 조회는 account-wide Estimated=true이며 이 추정을 실청구 확정액으로 바꾸지 않았다. 보존 EBS의 이후 추정비용 $0.055748/h는 계속 발생한다.
- 이번 요청의 두 GPU 실행과 문서 PR까지만 마감한다. 새 점검·분기·기동을 예약하지 않았고 전체 B 질문 완료는0이다. REQ-05 최종 모델/주체/퇴역, LIF-14 backend/RPC/출력 완료·stop-failure 후 재시작, 기존 A 센서·B 통합·환경/기업 정책 의존은 남는다.
- 문서 변경에는 제품 src/packs/tools/tests/의존성/CI 변경이 없다. 문서 정합성과 diff를 검증했으며 동일 소스의 전체 suite·GPU 시험을 다시 실행하지 않았다.
