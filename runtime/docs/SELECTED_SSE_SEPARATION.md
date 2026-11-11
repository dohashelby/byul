# REQ012 bounded SSE 인수

기존 `stream_probe.collect`, `assess_separation`, `record_for`를 재사용하는 별도 도구다. 기존 separation/ownership 결과와 UNKNOWN은 보존하고 이번 실행을 새 디렉터리에 저장한다. buffered 대조는 기존 ownership 통과 증거를 별도로 참조한다.

실행 한 번에 alpha와 beta 각각 한 번만 POST한다. alpha는 고유 marker를 공백으로 구분해 12회 출력하도록 요청한다. 사전 고정한 `generated-marker-close-v1`의 128 bytes/1 ms/최대32 reads를 사용한다. 완전 SSE 이벤트의 누적 generated content에 alpha marker가 포함되면 연결을 닫고 beta를 새 연결로 보낸다. 두 요청에는 같은 비신뢰 client label을 쓰며 실제 서버 UUID는 서로 달라야 한다. root가 250ms 대기를 시험하려면 별도 새 출력과 `--delay-ms 250`을 지정한다.

새 profile은 완전 이벤트의 choices.delta.content/text만 누적한다. JSON 오류·불완전 이벤트·metadata 필드의 marker는 semantic close 근거가 되지 않는다. 같은 read batch에 terminal이 이미 파싱됐으면 marker close로 승격하지 않는다. 최대 read 수까지 marker가 없으면 marker_read_limit로 닫고 UNKNOWN을 유지한다. 실제 alpha marker 관측과 terminal 전 닫힘은 원 판정기가 계속 검사한다. marker 전 닫힘·완료 후 닫힘·불완전 JSON/관측 부족은 UNKNOWN, 상대 marker 혼입과 정상 대조 실패는 기존 기준에 따라 FAIL이다. 0ms는 기존 collector 미지원이며 도구도 POST 없이 UNKNOWN으로 남긴다. 기존 slow-reader-v1 동작은 유지하며 --close-mode reads로 이전 정적 read-count 모드를 선택할 수 있다. 새 profile 때문에 collector와 최소 hash 소비부를 함께 갱신했다. root의 전체 source manifest/APPROVED_GATE pair는 별도로 갱신·검증한다.

```sh
# SOURCE는 root가 이미 검증한 실제 배포 source 절대경로.
# PY는 기존 venv-update Python, TOOL은 검토된 이 파일의 명시 경로.
# 기존 private 환경은 root process에만 로드하고 값은 argv/로그에 쓰지 않는다.
PYTHONPATH="$SOURCE/runtime:$SOURCE/runtime/src:$SOURCE/update/src" \
 "$PY" "$TOOL" --gateway http://127.0.0.1:18080 --model test-model \
 --inference-env RUNTIME_INFERENCE_TOKEN_A --viewer-env RUNTIME_VIEWER_TOKEN \
 --timeout 15 --delay-ms 0 --chunk-bytes 128 --close-after-reads 32 --interval-ms 1 --close-mode semantic \
 --out /명시/새로운/REQ012-SSE-인수
```

도구 자체를 기존 source 외부로 전송해도 위 PYTHONPATH에서 원 collector/판정기/원장 reader를 import한다. 해당 source의 해시와 도구 해시는 root 인수 기록에 함께 남긴다. 이 도구 때문에 Gateway 이미지를 다시 만들 필요는 없다. inference/viewer 참조가 없거나 보호 API·정책·정확 target instance·최소 출력 예산128·기존 capacity precondition이 확인되지 않으면 POST하지 않는다.

`helper.json`은 새 0700 디렉터리의 0600 파일이다. 원 판정 facts, 원장 UUID/상태/결정, 생성문 hash와 실제 close timing, 전후 scope를 저장하며 원 생성문·token은 저장하지 않는다. 마지막 `REQ012-SSE-SCOPE`도 함께 확인한다. 도구 exit0과 개별 PASS를 전체 REQ012 완료로 확대하지 않는다. 실행 중 scope 변동은 별도 FAIL/UNKNOWN이다. 엔진 취소·GPU 회수는 이 도구의 확인 범위에 추가하지 않는다.

로컬 검증: 실제 loopback SSE/원장 API에서 marker 이후 terminal 이전 닫힘, 상대 응답 혼입, 완료 후 닫힘, 잘못된 CASE JSON과 입력 경계를 시험했다. 짧은 marker 출력 뒤 semantic close, marker 미출력 UNKNOWN과 기존 read-count 모드도 확인했다. 정확한 새 회귀 수는 고정 검증 로그를 따른다. 실제 엔진/GPU/AWS 실행0회다.


새 profile의 marker 입력은 ALPHA+소문자16진수12자로 제한한다. read/time/body/event 한계는 기존 값을 유지한다. 같은 batch에서 DONE이 이미 관측됐거나 marker를 출력하지 않은 실행은 완료로 올리지 않는다. 도구의 기존 인자와 원 판정기는 보존하며 새 close mode만 추가했다.

source pin 영향은 update의 stream_probe.py raw hash, kubernetes_http.py의 BINDING_PROBE_SHA256, reviewed_http_contract.py의 raw2+wrapper literal이다. release/reviewed-source.json과 APPROVED_GATE/rootmanifest는 root 담당으로 이 변경에서 수정하지 않았다. 로컬 raw/wrapper 해시 일치와 과거 profile/잘못된 proof 거부를 별도 확인하며, 전체 manifest gate 통과는 root가 갱신한 최종 source에서 재검증한다.

semantic JSON은 parse_constant로 NaN/Infinity/-Infinity를 거부한다. 선택된 n=1 cohort에 맞춰 한 이벤트의 복수 choice 및 0이 아닌 choice index도 semantic marker 근거로 사용하지 않는다. 이 미지원/잘못된 JSON은 marker_json_valid=false이며 기존 판정의 UNKNOWN으로 남긴다. 기존 slow-reader와 원 assess_separation의 JSON 해석은 변경하지 않는다.
