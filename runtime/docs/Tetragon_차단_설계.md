# Tetragon 선택적 차단 설계안

상태: **방식별 후보·전환 도구·단위 시험 구현, 새 후보의 kind/AWS 차단 효과 미검증**. `tetragon_rule_modes`의 `TG-SECRET-01: enforce`와 `tetragon_enforce_method`를 함께 지정하면 아래 방식별 후보를 monitor 모드로 렌더한다. `tetragon_mode=enforce_probe`는 기존 시험 쓰기 규칙 `TG-DENY-01`만 제어한다. 이 문서의 이전 4회차 제안 중 정책 delete→apply와 확장자로 파일을 추정하는 절차는 아래 **구현 상태**와 5회차 확인순서로 대체한다.

## 1. 후보와 우선순위

1~7절은 4회차 전 설계·시험 제안을 보존한 기록이다. 현재 구현과 5회차 절차는 아래 **구현 상태**와 [5회차 확인순서](../deploy/gpu-lab/서버5회차_확인순서.md)를 우선한다.

Tetragon 훅은 커널 호출을 보며 `loading`·`compiling`·`inferring`은 별도 엔진 로그에서 만든 시간표다. 따라서 단계별 허용 목록을 커널 차단 선택자로 그대로 옮길 수 없다는 설계 판단이다. 근거: `runtime/src/serving_runtime/enforcement/tetragon.py:39-68`, `runtime/deploy/gpu-lab/ACCEPTANCE.md:141-145`; **추정/미확인:** 특정 단계와 커널 사건의 안전한 동기화 방식.

| 후보 | 잘못 막을 위험·정상 근거 | 이미 있는 통제 | Tetragon 훅·선택자 후보 | 권장 |
| --- | --- | --- | --- | --- |
| 보호 파일 읽기 | 엔진이 실제 비밀 경로를 필요로 하면 기동·요청 실패. AWS-10은 **가짜** 보호 파일의 엔진 읽기와 `TG-SECRET-01` read/monitor를 대조했으며, 정상 요청 대조는 AWS-11에 있다. 실제 비밀 경로의 정상 사용 여부는 **미확인**. 근거: `runtime/deploy/gpu-lab/ACCEPTANCE.md:135,167-168`. | 읽기 전용 mount는 쓰기를 막지만 읽기를 막지는 않는다. 보호 파일 읽기에 대한 별도 거부 통제는 **미확인**. 근거: `runtime/deploy/gpu-lab/ACCEPTANCE.md:163,167`. | 기존 `security_file_permission`의 `file` 경로 + `MAY_READ=4`, 엔진 `podSelector`; `TG-SECRET-01`을 `Override`로 확장. 현재 `Prefix`는 비슷한 경로까지 잡으므로 개별 파일은 `Equal` 검토. 근거: `runtime/src/serving_runtime/enforcement/tetragon.py:39-65,96-99`; **추정/미확인:** 조합 접근 mask 및 다른 읽기 경로의 포괄성. | **1순위, 좁은 경로부터** |
| 모델 폴더 쓰기 | 모델 갱신·임시 파일이 같은 경로에 있으면 오차단. AWS-06에서 `/models`는 `EROFS`, `/cache` 쓰기는 성공했다. 근거: `runtime/deploy/gpu-lab/ACCEPTANCE.md:163,180`. | 모델 RO mount, 별도 캐시 쓰기 경로. 근거: `runtime/deploy/gpu-lab/ACCEPTANCE.md:163,180`. | 기존 `TG-ASSET-01`: `security_file_permission`, 모델 경로 `Prefix`, `MAY_WRITE=2`, `Override` 후보. RO mount가 훅보다 먼저 거부하면 이벤트가 없을 수 있다. 근거: `runtime/src/serving_runtime/enforcement/tetragon.py:43-65,84-90`, `runtime/deploy/gpu-lab/ACCEPTANCE.md:180`. | 후순위, 중복 통제 효과 입증 후 |
| 캐시 밖 실행 | 정상 컴파일·첫 추론의 실행 파일이 허용 목록 밖으로 보일 수 있다. 2회차에는 vLLM loading·SGLang 첫 요청에서 오탐 6건, 3회차에는 단계 표지 전 실행 7건을 관찰했다. 근거: `runtime/docs/AWS_GPU_서버2회차_결과_20261104.md:43-44`, `runtime/docs/AWS_GPU_서버3회차_결과_20261104.md:52-55`. | 캐시 세대 mount와 `TG-CACHE-01` 쓰기 관측; 실행 차단 통제는 **미확인**. 근거: `runtime/src/serving_runtime/enforcement/intent.py:129-143`, `runtime/src/serving_runtime/enforcement/tetragon.py:79-90`. | `bprm_check_security` LSM 또는 실행 훅의 경로·부모 선택자는 **후보/미검증**. 현재 `TG-EXEC-01`은 `process_exec` 어댑터 분류이며 렌더된 차단 규칙이 아니다. 근거: `runtime/src/serving_runtime/enforcement/tetragon.py:74-75`, `runtime/src/serving_runtime/enforcement/events.py:261-310`. | 현재 감시 유지 |
| 셸 실행 | 3회차 SGLang 정상 빈 캐시 컴파일 중 `/bin/sh` 36건. 일괄 차단하면 정상 기동을 깨뜨린다. 근거: `runtime/docs/AWS_GPU_서버3회차_결과_20261104.md:46-55,62-63`. | 의심 탐지 `suspect_binary`; 부모 설명은 있어도 의심 판정 유지. 근거: `runtime/deploy/gpu-lab/ACCEPTANCE.md:5-7`. | 실행 훅 + `matchBinaries`/`matchParentBinaries` 후보이나 단계 선택 불가·부모 변동 위험. **추정/미확인:** 커널 규칙의 정확한 선택자 조합과 비용. | **차단 비권장**, 감시 |
| 외부 송신 | 차단 오류와 원격 장애를 혼동할 위험. AWS-05는 허용 대조 연결과 Hubble `dropped`를 함께 요구한다. 근거: `runtime/deploy/gpu-lab/ACCEPTANCE.md:162,176`. | **Cilium 담당**. AWS 적용 규칙과 실제 패킷 차단 주체는 Cilium. 근거: `runtime/deploy/gpu-lab/ACCEPTANCE.md:49,186`. | Tetragon 네트워크 훅·선택자는 이번 범위에서 **미검증**이며 중복 집행 대상에서 제외. | Tetragon 차단 비권장 |

**결정 제안:** `TG-SECRET-01`의 파일 읽기만 첫 실집행 후보로 삼는다. AWS-10의 가짜 파일로 같은 Pod·경로·접근 종류를 대조할 수 있고, 모델 쓰기는 이미 RO mount로 거부된다. 셸·실행 차단은 정상 컴파일 관찰 때문에 이 단계에서 선택하지 않는다. 근거: `runtime/deploy/gpu-lab/ACCEPTANCE.md:163,166-168`, `runtime/docs/AWS_GPU_서버3회차_결과_20261104.md:50-55`. 실제 비밀 파일을 대상으로 한 적용은 **미확인**이며 AWS-10의 통과는 탐지 통과일 뿐 차단 통과가 아니다. 근거: `runtime/deploy/gpu-lab/ACCEPTANCE.md:167`.

## 2. intent·승인·묶음 설계

1. **제안 형식:** 기존 `tetragon_mode`는 유지하고, 1.1 intent의 선택 필드 `tetragon_rule_modes`를 `{ "TG-SECRET-01": "monitor" }` 같은 규칙 ID → `monitor|enforce` 객체로 추가한다. 허용 ID는 렌더 가능한 운영 파일 규칙의 명시 목록으로 고정하고 알 수 없는 ID·값을 거부한다. 필드가 없으면 기존 동작을 그대로 따른다. 기존 `tetragon_mode`는 시험 규칙 `TG-DENY-01`만 제어하고 새 필드는 운영 규칙만 제어한다. 따라서 기존 `monitor`에서도 `TG-SECRET-01: enforce`를 명시할 수 있고, `enforce_probe`가 운영 규칙의 `enforce`를 암시하지 않는다. `TG-DENY-01`을 새 필드에 넣는 것은 거부한다. 근거: `runtime/src/serving_runtime/enforcement/intent.py:366-411,465-472`, `runtime/src/serving_runtime/enforcement/tetragon.py:74-109`; **제안/미구현**.
2. `TG-SECRET-01`은 초기 기본값 `monitor`; 명시된 `enforce`에서만 Override를 렌더한다. `TG-ASSET-01`·`TG-CACHE-01`은 선택 필드에 이름을 넣더라도 1차 구현에서는 `enforce`를 거부하고 관찰만 유지한다는 제안이다. 지원 범위를 코드·문서·시험이 일치시켜야 한다. 근거: `runtime/src/serving_runtime/enforcement/tetragon.py:84-109`; **제안/미구현**.
3. 모드 변경은 intent 바이트를 바꾸므로 기존 묶음의 `enforcement_intent` 파일 해시·크기와 맞지 않는다. 원본 승인 정책에 intent를 넣은 구성이라면 `runtime.enforcement.intent`와 서버 intent의 정규 JSON도 일치시켜야 한다. 승인 판본·해시·승인 참조는 담당자와 다시 합의하고, 확정 원본으로 `bundle build` → `bundle verify` → 적용 검증을 한다. 파일만 수동 교체하거나 기존 승인으로 새 집행을 승인했다고 간주하지 않는다. 근거: `runtime/src/serving_runtime/bundle_tools.py:119-127,149-177`, `contracts/schemas/policy-bundle.schema.json:13-40`, `runtime/deploy/gpu-lab/정책_수동적용_절차.md:1-3,11-27,40-64`.
4. **계약 판단:** 위처럼 intent **1.1의 선택 필드**로 제한하면 `contracts/schemas/policy.schema.json`은 intent 본문을 일반 객체로 받아 내부 검증을 runtime에 맡기고, 묶음 계약도 `enforcement_intent` 역할과 파일 해시를 이미 지원하므로 **현재 계약 파일 변경은 필수 아님**. 근거: `contracts/schemas/policy.schema.json:94-105`, `contracts/schemas/policy-bundle.schema.json:20-40`, `contracts/README.md:118-122`. 다만 intent 버전을 1.2로 올리거나, 개별 Tetragon 사건에 새로운 `blocked` outcome/거부 errno 필드를 추가하려면 계약 enum·화면·변환 규칙 변경이 필요해 **합의 필요**. 근거: `contracts/schemas/policy.schema.json:103`, `contracts/schemas/runtime-event.schema.json:66-78,203-233,354-366`, `contracts/README.md:87-99`.

## 3. 렌더러·커널 행위

- `TG-SECRET-01`의 `security_file_permission`은 지금 `file` 경로 `Prefix`와 정수 mask `4`를 `matchArgs`에 둔다. 첫 차단 규칙은 가짜 파일 한 경로에 대해 `Equal`과 `Post`+`Override`로 후보를 만들고, 적용 전 렌더 manifest의 selector·대상 namespace·엔진 label을 사람이 확인한다. `MAY_READ|MAY_WRITE` 같은 복합 mask, 링크/다른 열기 방식, 경로 접두 오인식은 **미확인**이므로 별도 음성·양성 시험으로 닫는다. 근거: `runtime/src/serving_runtime/enforcement/tetragon.py:39-68,96-99`; **제안/미구현**.
- `argError`는 커널 함수에 돌려줄 **음수 errno**로 지정한다. 현재 `-1`은 `-EPERM`이고 L-05에서 시험한 값이다. 가짜 파일 읽기에는 `-EACCES`(`-13`)가 의미상 후보이나, 기존 증거와 단순 비교를 위해 첫 시험은 `-EPERM`(`-1`)을 제안한다. 어느 값이 사용자 공간 `open/read`에서 그대로 관측되는지는 해당 환경에서 확인해야 한다. Tetragon 1.7.1 공식 [selectors 문서](https://github.com/cilium/tetragon/blob/v1.7.1/docs/content/en/docs/concepts/tracing-policy/selectors.md#override-action-for-kprobe)는 `matchActions: Override`, `argError: -1`, 함수 반환값 대체를 설명한다. 근거: `runtime/src/serving_runtime/enforcement/tetragon.py:22-25`, `runtime/docs/상세_안내.md:570`; **추정/미확인:** 보호 파일 읽기의 실제 errno.
- `Override`는 호출 함수 대신 오류를 반환하므로 읽기 동작 자체를 거부하는 목적에 적합하다. `Sigkill`은 프로세스를 끝내지만 해당 호출의 부작용을 항상 막지는 못하고 정상 서빙을 중단시킬 위험이 크다. 첫 구현은 Override를 선택하며 Sigkill은 별도 위험 승인·시험 없이는 쓰지 않는다. 근거: Tetragon 1.7.1 공식 [selectors](https://github.com/cilium/tetragon/blob/v1.7.1/docs/content/en/docs/concepts/tracing-policy/selectors.md#override-action-for-kprobe), 공식 [enforcement 개념](https://tetragon.io/docs/concepts/enforcement/); **추정/미확인:** 엔진이 파일 읽기 오류를 처리하는 방식.
- 원시 `process_kprobe.action`이 `KPROBE_ACTION_OVERRIDE`이면 현재 `events.py`는 `action_mode: enforce`, `KPROBE_ACTION_POST`이면 `monitor`로 정규화하고 `action_raw`를 보존한다. 플랫폼 변환은 `enforce`를 `enforce_attempted`로 내보낸다. 그러므로 **Override 이벤트만으로 차단 성공 판정 금지**. 읽기 호출의 거부 errno, 동일 경로·Pod·규칙·시각의 원시 이벤트, 전후 정상 요청을 함께 판정한다. 근거: `runtime/src/serving_runtime/enforcement/events.py:342-403`, `platform/api/src/byul_platform/sources/runtime.py:200-240`, `contracts/README.md:87-99`.

## 4. 화면과 판정

현재 공통 배지는 Tetragon을 `탐지`로 표시하고, Tetragon 관측 화면은 Override를 `차단 시도 기록(enforce)`로 표시한다. 계약의 `enforce_attempted`도 결과 미확정이다. 근거: `platform/web/src/pages/runtime/parts.tsx:100-106`, `platform/web/src/pages/runtime/RuntimeObservePage.tsx:218-242`, `platform/web/src/lib/labels.ts:10-21`, `contracts/schemas/runtime-event.schema.json:66-78`.

화면 변경 제안은 **규칙·시험 단위**로 적용한다. Tetragon 목록의 `Post` 행은 `탐지`, `Override` 행은 `차단 시도`로 유지한다. 별도 검증 시험 행에 동일 Pod UID·규칙 ID·정확한 경로·read·시간창과 거부 errno, 이전/이후 정상 요청 200이 결합되면 `차단 확인` 배지를 표시한다. 대상 불일치·이벤트 누락·수집 gap·정상 요청 실패는 `unknown` 또는 `failed`로 표시한다. 공통 `denied` outcome은 현재 Gateway 전달 거부 뜻이므로 개별 Tetragon 이벤트에 재사용하지 않는다. 사건 자체에 `차단` 배지를 달려면 계약 의미·증거 링크·UI 문구에 대한 **platform 합의 필요**. 근거: `contracts/schemas/runtime-event.schema.json:5,66-78`, `contracts/README.md:77-99`, `platform/api/src/byul_platform/sources/runtime.py:200-228`; **제안/미구현**.

## 5. 배포·즉시 해제·되돌리기

1. 새 intent를 **monitor**로 승인·묶음 생성·검증한 뒤 후보 manifest를 검토하여 배포한다. 현재 방식대로 엔진 Pod·정확한 경로의 읽기 이벤트, 누락·지연·정상 요청을 관찰한다. 관찰 기간은 최소 한 번의 빈 캐시 기동과 정상 요청 구간을 포함하되 운영상 시간 길이는 **미결**이다. 2·3회차에서 첫 기동과 캐시 있는 기동의 실행 양상이 달랐다. 근거: `runtime/docs/AWS_GPU_서버2회차_결과_20261104.md:43-45`, `runtime/docs/AWS_GPU_서버3회차_결과_20261104.md:46-55`; **제안/미구현**.
2. 실제 엔진이 해당 경로를 정상적으로 읽지 않는다는 증거와 커널 지원, 관찰 수집 상태, 되돌리기 담당·절차를 확인한 뒤 `TG-SECRET-01: enforce`의 새 intent·묶음으로 전환한다. `bundle build`·`bundle verify`를 다시 하고 Tetragon 정책 객체도 새 렌더본으로 적용·대조한다. 묶음 전환만으로 이미 적용된 Tetragon 객체가 바뀐다고 가정하지 않는다. 근거: `runtime/src/serving_runtime/bundle_tools.py:155-179`, `runtime/src/serving_runtime/enforcement/tetragon.py:26-71`, `runtime/deploy/gpu-lab/README.md:98`; **제안/미구현**.
3. **즉시 해제 명령 예시**(실행 환경의 context·namespace 확인 후):

   ```bash
   kubectl --context <context> -n <namespace> delete tracingpolicynamespaced runtime-tg-secret-01
   kubectl --context <context> -n <namespace> get tracingpolicynamespaced runtime-tg-secret-01
   ```

   또는 승인된 monitor intent로 새 묶음을 만들고 `bundle verify` 후 monitor manifest를 `kubectl --context <context> -n <namespace> apply -f <monitor-manifest.json>`로 되돌린다. 삭제는 집행을 즉시 걷어내기 위한 선택이고, monitor 복구는 관측을 다시 켜기 위한 선택이다. 실제 정책 객체 이름은 렌더러의 `runtime-{rule_id.lower()}`에서 나온다. 삭제·적용 전후 객체 내용과 대상 Pod를 재확인한다. 근거: `runtime/src/serving_runtime/enforcement/tetragon.py:26-35,39-65`; **제안/미실행**.
4. 되돌리기 시험은 enforce 상태에서 가짜 파일 읽기 거부를 확인한 다음 monitor로 되돌려 같은 가짜 파일 읽기 성공·`Post` 탐지·정상 요청 200을 확인한다. 다시 enforce가 필요한 경우 새 승인·묶음·시험을 거친다. L-06은 종전 기록에서 실행되지 않았으므로 이 절차의 선례로 간주하지 않는다. 근거: `runtime/docs/상세_안내.md:570`, `runtime/docs/인수시험_대응표.md:25-26`; **제안/미실행**.

## 6. 시험 계획과 증거

| 단계 | 절차 | 통과 기준·증거 |
| --- | --- | --- |
| 로컬 kind: L-05 확장 | 기존 정확 경로 쓰기 `TG-DENY-01`의 `EPERM` 대조를 보존하고, 별도 가짜 보호 파일을 `TG-SECRET-01` monitor→enforce→monitor로 시험한다. 규칙 적용 전후와 정책 삭제 후 읽기를 반복한다. 근거: `runtime/docs/상세_안내.md:570-582`, `runtime/src/serving_runtime/enforcement/tetragon.py:96-108`; **제안/미실행**. | enforce 구간의 거부 errno와 `KPROBE_ACTION_OVERRIDE`, 같은 파일 read·Pod UID·rule ID·정책 이름·시각이 일치. monitor/삭제 구간은 읽기 성공과 `KPROBE_ACTION_POST`, 정상 요청 200. 하나라도 빠지면 `unknown`; 예상과 다른 차단 또는 정상 요청 실패면 `failed`. **제안/미검증**. |
| AWS-10 변형: 새 사례 ID 합의 필요 | 기존 AWS-10은 탐지 기준 그대로 보존하고, 새 opt-in 사례로 가짜 파일 읽기 거부를 시험한다. 실제 비밀 내용·경로는 증거로 반출하지 않는다. AWS-01·11을 직전·직후 실행하고 두 엔진 및 빈 캐시 기동을 관찰한다. 근거: `runtime/deploy/gpu-lab/ACCEPTANCE.md:158,167-168`, `runtime/docs/AWS_GPU_서버3회차_결과_20261104.md:46-63`; **제안/미실행**. | 가짜 파일에서 예상 `EPERM` 또는 합의한 errno, 같은 시간·경로·Pod UID의 Tetragon Override 원시 이벤트, `action_mode=enforce`/`enforce_attempted`, 같은 시간창의 정상 chat HTTP 200·Gateway `ALLOWED`·엔진 수신, AWS-11의 정상 stream·캐시 쓰기와 잘못된 경고 0건. 이벤트만 있거나 errno만 있으면 `unknown`; 정상 요청 회귀면 `failed`. 근거: `runtime/src/serving_runtime/enforcement/events.py:371-399`, `platform/api/src/byul_platform/sources/runtime.py:200-228`, `runtime/deploy/gpu-lab/ACCEPTANCE.md:158,167-168`; **제안/미검증**. |

증거 최소 필드: 시험 ID·환경·엔진 종류·intent/묶음 해시·정책 객체 UID/적용 시각, 대상 Pod UID·container·규칙 ID·정확한 파일 경로의 **가린 식별자**·read mask·원시 `action`·`action_mode`·원시 반환값(있는 경우), 호출 errno 번호/이름, 요청 ID·HTTP 코드·Gateway final·엔진 수신 대조, 수집 gap 여부, 되돌리기 시각·재시도 결과. 파일 내용·실명·IP·토큰은 기록하지 않는다. 원시 Tetragon 반환값과 사용자 공간 errno의 일치 방식은 **미확인**. 근거: `runtime/src/serving_runtime/enforcement/events.py:342-403`, `runtime/deploy/gpu-lab/ACCEPTANCE.md:158,167-168`, `contracts/schemas/runtime-event.schema.json:31-42,203-233`.

## 7. 미결 사항과 위험

- **정식 커널 차이:** 2회차 AWS에서는 Tetragon 1.7.1의 `override_return`·`fmodret` 가능성을 읽기 전용으로 확인했지만 실제 차단은 시험하지 않았다. 로컬 kind 결과를 AWS나 정식 클러스터에 전이하지 않는다. 근거: `runtime/docs/AWS_GPU_서버2회차_결과_20261104.md:45`, `runtime/docs/상세_안내.md:570`.
- **지원 조건:** Tetragon 1.7.1 공식 [selectors 문서](https://github.com/cilium/tetragon/blob/v1.7.1/docs/content/en/docs/concepts/tracing-policy/selectors.md#override-action-for-kprobe)는 kprobe Override에 `CONFIG_BPF_KPROBE_OVERRIDE`와 오류 주입 가능한 함수, `security_` 훅은 커널 5.7 이상을 적는다. 실제 노드에서 `tetra probe config`·오류 주입 가능 함수 목록·정책 load 상태·실제 errno를 확인해야 한다. BPF LSM은 AWS 2회차 부팅 목록에 없어 첫 후보로 두지 않는다. 근거: `runtime/docs/AWS_GPU_서버2회차_결과_20261104.md:45`; **미확인:** 정식 노드의 설정·훅 지원·부팅 옵션.
- **보호 경로 사용:** 엔진이 진짜 비밀 파일을 읽는 경우 Override가 정상 동작을 깨뜨릴 수 있다. 가짜 파일 AWS-10은 이를 배제하지 못한다. 파일 경로 접두·링크·복합 mask·다른 API의 우회나 중복 이벤트도 시험 전까지 **미확인**. 근거: `runtime/deploy/gpu-lab/ACCEPTANCE.md:167`, `runtime/src/serving_runtime/enforcement/tetragon.py:53-63`.
- **관측과 집행의 차이:** Tetragon 이벤트 `Override`는 시도 근거다. 차단이 확인되려면 읽기 호출 결과와 같은 대상의 대조가 필요하다. 수집 중단·gap이 있으면 사건 부재를 허용/차단 증거로 쓰지 않는다. 근거: `contracts/README.md:87-99`, `runtime/docs/AWS_GPU_서버3회차_결과_20261104.md:40-44`.

## 구현 상태 (2026-11-05)

- intent 1.1에 `protected_paths.enforce_exact_files`를 추가했다. 명시한 파일은 확장자가 없어도 받으며 상위 디렉터리·접두 경로는 거부한다. 새 방식 지정에는 이 목록이 필수다. 이전 intent는 그대로 읽고 렌더한다. 컨테이너 안 파일 유형과 링크 대상은 로컬 검증만으로 증명할 수 없다.
- `tetragon_enforce_method` 선택값은 `override_lsm`(`security_file_permission`, Override -EPERM + Sigkill), `sigkill_fd_install`(`fd_install`, Sigkill), `override_syscall`(`sys_openat`, Override -EPERM + Sigkill)이다. 모두 엔진 Pod selector의 `TracingPolicyNamespaced` 후보이며 `spec.options.policy-mode=monitor`로 시작한다. Tetragon **1.7.1**의 정책 모드는 `monitor`·`enforce` 두 가지다. `monitor_only`는 이 버전의 전환값으로 쓰지 않는다. [1.7.1 정책 모드 문서](https://github.com/cilium/tetragon/blob/v1.7.1/docs/content/en/docs/concepts/tracing-policy/mode.md), [selectors 문서](https://github.com/cilium/tetragon/blob/v1.7.1/docs/content/en/docs/concepts/tracing-policy/selectors.md).
- `override_syscall`은 경로 문자열을 대조하므로 상대 경로·심볼릭 링크·경합 우회 한계가 있다. Sigkill은 엔진 프로세스 종료를 일으킬 수 있다. 엔진 종료가 실제로 일어나면 Gateway 대상 확인 실패와 요청 503(엔진 미전달), Kubernetes 재시작 및 복구를 별도로 확인해야 한다. Sigkill 이벤트 하나만으로 파일 동작이 막혔다고 판정하지 않는다. [1.7.1 enforcement 문서](https://github.com/cilium/tetragon/blob/v1.7.1/docs/content/en/docs/concepts/enforcement/_index.md).
- `runtime/tools/tetragon_mode.py`는 `tetra tp set-mode --namespace` 뒤 STATE·MODE·정책 ID를 확인하고 실패하면 원래 모드로 돌려 확인한다. 규칙 교체는 새 이름으로 적용·enabled 확인 뒤 이전 규칙을 삭제한다. L-05b와 AWS-20은 같은 정책 모드 전환을 사용한다. L-05c는 `--sigkill-probe-check`로 분리한 시험용 Pod에서 실행한다. `aws_lab_preflight.py --override-check`는 커널 설정·LSM·오류 주입 심볼·`tetra probe` 근거로 방식별 가능/불가/확인 필요를 출력한다.
- AWS-20은 방식별 errno 또는 종료 코드와 같은 Pod UID 원시 이벤트, 정상 요청 또는 엔진 대상 503·복구, monitor 복귀를 판정한다. Override+Sigkill을 함께 쓸 때 사용자 공간 errno가 실제로 관측될지는 미확인이다. 현재 `kubectl exec` 읽기 probe는 엔진의 주 프로세스가 아니므로 엔진 대상 503·재시작의 증거가 될 수 없다. 5회차에서는 별도 엔진 주 프로세스 시험 절차를 마련하기 전 해당 항목을 통과로 기록하지 않는다.

## 다음 확인

1. 로컬 kind L-05b/L-05c에서 정책 load·MODE·동일 ID·원시 이벤트·파일 결과를 실측한다. WSL 커널에서 load 불가면 지원 불가 근거를 남기고 단위 시험 결과와 구분한다.
2. 5회차 서버에서는 사전 점검 뒤 시험용 Pod부터 방식별 후보를 확인한다. 엔진 주 프로세스 대상 시험은 별도 opt-in이며 Gateway 503·재시작·복구 증거가 있어야 한다.
3. 화면의 Override/Sigkill 표지는 **차단 시도**다. 실제 차단 판정은 파일 결과와 수집 상태를 함께 확인한다.
