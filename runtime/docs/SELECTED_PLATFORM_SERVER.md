# Fresh 인수용 읽기 전용 Platform

root가 최종 검증 소스·현재 Gateway loopback·메모리 viewer 환경변수·새 Platform store·명시 UPDATE reports·검증한 Lab report 경로를 제공한다. `selected_platform_server.py`는 기존 Settings/create_app과 static build를 사용하고 GET/HEAD만 허용한다. 기존 Platform API/model/generated 파일은 바꾸지 않는다.

```sh
cd /ABS/final-frozen-source
export PYTHONPATH=runtime:runtime/src:update/src:platform/api/src
$PY runtime/tools/selected_platform_server.py \
  --source /ABS/final-frozen-source --source-sha FULL40 \
  --gateway-url http://127.0.0.1:18080 --viewer-token-env RUNTIME_VIEWER_TOKEN \
  --platform-data-dir /NEW/RuntimeData/platform \
  --update-reports-dir /READONLY/validated-update-reports \
  --lab-report /READONLY/validated-lab-report.json \
  --receipt /NEW/public/platform-process.json --port 18700
```

`platform-data-dir`의 부모와 receipt의 부모는 존재해야 하고 store/receipt 자체는 새 경로여야 한다. UPDATE reports는 이미 존재하는 명시 디렉터리로 읽는다. 빈 UPDATE reports라도 example source로 전환하지 않는다. evidence는 자신의 새 빈 file index를 사용한다. root가 같은 Lab report를 Gateway 기동 설정에 연결하며 Platform은 기존 `GET /api/lab-report`를 소비한다. 입력 Lab report를 지정하면 기존 `LabReportSource._validate`로 확인하고 API 응답을 로컬 파일로 대체하지 않는다. Gateway 계획의 lab report가 아직 null이면 `--lab-report`를 생략해 그 미설정 상태를 그대로 표시한다. canonical RPT는 명시 UPDATE reports 디렉터리에서 기존 adapter로 읽으며 L-01~L-06 결과로 변환하지 않는다. 필수 OBS002의 실제 L6 시험과 report 연결은 별도 단계로 완료한다.

`SOURCE_SHA` 또는 읽을 수 있는 로컬 Git HEAD와 source-sha를 대조한다. runtime/API 실제 `__file__`가 source 안의 정확 파일이어야 하고 built index/JS/CSS가 있어야 한다. UPDATE 추가 때문에 전체 SHA가 바뀌면 해당 최종 source-sha로 호출한다. runtime/API/web 불변의 근거는 각 파일/asset SHA로 따로 남긴다.

uvicorn이 실제 listen을 시작한 뒤에만 새 receipt를 쓴다. receipt는 `source_sha`, `pid`, runtime/API import `{file,sha256}`, `web_dist_sha256`, `host`, 실제 `port`, `runtime_base_url`, `read_only`, `listening`을 포함한다. `selected_acceptance_run.py`의 Platform receipt 입력으로 그대로 사용할 수 있다. viewer 값·환경변수 이름·Lab 원문·실제 기록은 receipt에 없다. access log와 startup source 로그는 끈다. 실패는 예외 종류만 출력한다.

## 기존 Chrome capture 재사용

기존 공개 helper `evidence/dev-a/remaining/ui-preparation/headless_ui_acceptance.py`의 `capture` 함수만 import한다. `main`의 합성 fixture 생성은 호출하지 않는다. root는 Gateway/Platform 포트를 같은 세대에 고정한 상태에서 A observe/helper의 전후 scope 보고서와 이 receipt를 같은 phase에 연결한다. 아래는 root의 로컬 Chrome에서 수행할 argv 묶음이다.

```sh
$PY - /PUBLIC/headless_ui_acceptance.py http://127.0.0.1:18700 \
  /NEW/public/ui-VLLM-gap VLLM-gap /PUBLIC/platform-process.json \
  /PUBLIC/observe-gap-run.json <<'PY'
import hashlib,importlib.util,json,sys
from pathlib import Path
helper,base,out,phase,receipt,scope_report=sys.argv[1:]
spec=importlib.util.spec_from_file_location('byul_live_capture',helper)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
output=Path(out);output.mkdir(exist_ok=False)
rows=[module.capture(base+'/runtime/observe/collectors?period=all',phase+'-collectors',output,['수집 상태']),
      module.capture(base+'/runtime/observe/checks?period=all&check=CHK-OBS-012',phase+'-checks',output,['선정 37개','CHK-OBS-012'])]
# collectors 화면에 현재 관리/경로 거부 기록 패널도 함께 있다.
result={'mode':'live-loopback-capture','phase':phase,'security_verdict':None,
        'capture_helper_sha256':hashlib.sha256(Path(helper).read_bytes()).hexdigest(),
        'server_receipt_sha256':hashlib.sha256(Path(receipt).read_bytes()).hexdigest(),
        'scope_report_sha256':hashlib.sha256(Path(scope_report).read_bytes()).hexdigest(),
        'captures':rows,'screenshots_visually_reviewed':False}
(output/'capture.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
PY
```

baseline/gap/recovered와 각 helper 모드의 단계마다 새 output/phase를 사용한다. 실제 request 상세는 동일 helper의 `capture(base+'/runtime/requests?period=all&sel='+SERVER_UUID,...)`로 정확 UUID를 고른다. source·same run/engine은 A helper의 전후 판정과 root의 Pod/container/forward 근거에서 대조하며 Chrome 함수가 자체 확인한 것으로 표시하지 않는다. 반환된 `passed`는 DOM 문구/렌더 확인 범위다. root/R2가 PNG를 직접 보고 실패/UNKNOWN/미관측 표시를 함께 확인한다.

공개 반출은 receipt·phase capture.json·선정 PNG/HTML/TXT만 명시한다. 원 feed/store/reports/private auth 디렉터리를 재귀 반출하지 않는다. 실제 기동/AWS·live 캡처는 root가 수행한다. 새 도구의 로컬 시험은 실제 ASGI/static route·readonly 거부·import/assets와 uvicorn startup 대역을 검사하며 실제 listen/GPU 완료로 세지 않는다.
