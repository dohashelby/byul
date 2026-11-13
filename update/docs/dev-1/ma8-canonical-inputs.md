# SUP-08 정본 입력 준비

정본 팩 @34의 SUP-08 revision 5를 사용한다. 판본·이미지는 작업지시서 0.1절에서 읽는다. 기준·후보는 같은 Phi-3 Vision 파생 모델 inventory, 승인/미승인 코드 commit, GPU·드라이버·TP/PP/DP와 비교 조건을 사용한다. 기동 명령과 실행별 import 표식 경로 차이만 정규화한다.

기존 PoC의 `product_update`는 예상 동작의 근거다. 새 실행의 EVD·RES·RPT로 복사하지 않는다. Python 설치 레이어 확인과 엔진 설치 bytes 검토, GPU 실행은 서로 다른 단계다.

1. 기존 `tools.prepare_sup08_phi3v_fixture`로 고정 모델의 실제 파일을 검증하고 새 fixture를 만든다. 큰 모델 다운로드는 별도 승인 대상이다.
2. `tools.gpu_a_aws_lab`의 코드 선택 세대로 각 판본을 렌더링한다. 같은 fixture의 모델·승인 코드·trust 설정을 넣고 실행별 marker 경로를 분리한다. 실제 기동은 GPU 세션 승인 뒤 진행한다.
3. 실제 설치 판본·GPU·드라이버·모델 inventory를 확인한 ENV와 실제 namespace/Pod에서 조립한 target을 준비한다. 예제 Pod를 실제 입력으로 표시하지 않는다.
4. 아래 조립기는 외부 IO 없이 두 입력을 대조한다.

```sh
uv run python -m tools.mvp_sup08_inputs \
  --fixture <실제-fixture-manifest.json> --control-image <고정-data:image/png;base64,값> \
  --baseline-env <기준-ENV.json> --baseline-target <기준-target.json> \
  --candidate-env <후보-ENV.json> --candidate-target <후보-target.json> \
  --output <새-입력-폴더>
```

출력은 쪽별 environment.json·target.json·policy.yaml·guard.toml과 전체 manifest.json이다. 기존 Kubernetes 입력 검증과 정책·관측 설정 계약을 적용한다. prepared 관측기 자산은 이 HTTP 코드 선택 경로에서 요구하지 않는다. 모델 bytes를 다시 읽었다거나 클러스터를 관측했다고 주장하지 않는다. 정책은 DECLARED/DRAFT이며 확정자·실제 결과·비교 결과는 비어 있다.

단위 시험은 모의 ENV·Pod와 작은 EXAMPLE inventory로 정상 조립, 모델/코드/이미지/GPU/trust 불일치 거부, 과거 예상의 새 판본 상속 금지를 검증한다. 실제 모델 fixture·클러스터 입력이 이 작업 공간에 없어 정본 실행 입력의 최종 조립은 아직 하지 않았다. J02에서 새 입력을 조립하고 정본 팩으로 새 EVD→RES→RPT를 생성해야 한다.
