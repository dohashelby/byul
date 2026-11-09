# 고정 SGLang HTTP 앱의 종결 관측 연결

`sglang_termination_launcher.py`는 SGLang0.5.22 기본 단일-tokenizer HTTP 배포에서만 선택하는 얇은 launcher다. 호출 자체가 opt-in이며 기존 SGLang 명령/패키지 파일은 바꾸지 않는다. 기존 lifecycle bootstrap이 등록한 `sys.modules['guard_lifecycle_sensor']`를 그대로 사용해 HTTP middleware와 native `_init_req_state` hook이 같은 ContextVar·Observer를 소비한다.

## 고정 경로 근거

공식 `v0.5.22`의 [launch_server.py](https://raw.githubusercontent.com/sgl-project/sglang/v0.5.22/python/sglang/launch_server.py)는 기본 HTTP 분기에서 `sglang.srt.entrypoints.http_server.launch_server`를 실행한다. [http_server.py](https://raw.githubusercontent.com/sgl-project/sglang/v0.5.22/python/sglang/srt/entrypoints/http_server.py)는 모듈의 FastAPI `app`을 만들고, 단일-tokenizer 기본 경로에서 같은 객체를 Uvicorn Config에 넘긴다. 이 두 entry 파일에는 vLLM식 `--middleware` 옵션 연결이 없다. 기존 `app.add_middleware` API로 연결하고 `runpy`로 기존 launch entrypoint를 유지한다.

보존 원문과 URL/hash는 `tests/fixtures/sglang0522-launch-source.zip`의 MANIFEST에 있다. 실행기는 설치 버전0.5.22, launch 파일 `374976475a62ee53a3f32454bf17071eb9b18bab7ed293390e2c765e3669a7a2`, HTTP 파일 `a89d20bb2fa8a86c511ab4d9d054f74d707423ab220f733ff25e62fcc0e49979`를 실제 설치 파일과 대조한다. 실제 OCI 내부 byte가 다르면 이 launcher의 실행을 멈추고 해당 불일치를 기록한다. 고정 tag에서 확인한 후보를 image 내부 동일성의 실측으로 대신하지 않는다.

## 기동 입력과 연결 순서

기존 C lifecycle 자산 생성·검증 경로가 아래 파일을 읽기 전용으로 배치한다. 기존 asset manifest를 A가 수정하지 않는다.

- `guard_lifecycle_sensor.py`와 base/settings/sitecustomize: 현재 SG native profile의 기존 자산
- `guard_sglang_launcher.py`: 이 launcher 파일의 정확 byte

bootstrap이 먼저 센서를 설치해야 한다. launcher는 정확 `/guard-lifecycle/guard_lifecycle_sensor.py`·profile/schema·활성 Observer와 sensor SHA `83b071c1980eb055a650214aba516f4d1011ad675c89df2b84ecc7c67275de88`을 확인한다. sensor를 새로 import해 두 번째 `_REQUEST`/`_BASE`를 만드는 경로는 없다. 그 뒤 source gate→HTTP 모듈 import→아직 시작하지 않은 원 `app`에 같은 sensor의 Middleware1개→기존 entrypoint 순서다.

```sh
# root/C가 기존 lifecycle assets와 PYTHONPATH sitecustomize를 연결한 배포에서
python3 /guard-lifecycle/guard_sglang_launcher.py \
  --model-path /srv/serving/models/7ae557604adf67be50417f59c2c2f167def9a775 \
  --served-model-name test-model --host 0.0.0.0 --port 8000 \
  --disable-overlap-schedule
```

실제 배포의 기존 고정 args를 그대로 전달한다. wrapper용 새 옵션이나 인증 인자는 추가하지 않는다. Python package 형태로 선택할 때는 `python -m guard.adapters.sglang_termination_launcher`도 같은 동작이다. root/C는 기존 source/image/model·단일 tokenizer/HTTP·TP/PP/DP1 프로필의 완전한 args와 asset manifest를 보존한다. 다중 tokenizer의 spawn/re-import와 gRPC/Ray/encoder 분기는 이 연결 프로필 밖이다. native profile의 runtime 자격 검사는 기존 센서가 수행한다.

## 검증과 실제 인수

CPU 회귀는 고정 HTTP 파일의 app 생성 AST와 실제 FastAPI ASGI, 고정 tokenizer 파일의 `_init_req_state` 함수 원문, 현재 SG sensor/Observer를 연결한다. 실제 `http.disconnect`가 전달됐을 때 ASGI invocation/request hash와 native RID hash·state가 한 context로 기록되는지 확인한다. 기본 dispatch 원문, source 변경/버전/미설치/이미 시작한 app 거부, 동일 sensor1회 middleware, runpy args/예외 보존을 함께 시험한다. 이는 단편 원문/CPU 계약 검사이며 SGLang 전체 launch·GPU 실행은 후속이다.

검증 snapshot은 root SG `bd78fefe337dd54df5ddaac369d534524f5cc798` 고정 archive에 A 신규 launcher/test/fixture만 겹친 별도 사본이다. sensor/projector/registry/parser byte는 그대로다. 로컬19개 통과(0.39초), 실제 수입 파일 경로와 sensor SHA를 확인했다.

root의 실제 인수는 같은 OCI에서 source gate 통과와 실제 기동→정상 요청→client close→native RID 연결/종결 capture를 기존 C collector로 확인한다. source 불일치·누락·미귀속은 해당 상태로 남긴다. ASGI 연결만으로 엔진 취소/GPU 회수나 전체 보안 판정을 만들지 않는다.
