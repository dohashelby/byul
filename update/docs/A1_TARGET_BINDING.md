# Docker 엔진 소스의 ENV·소유 신원 결합

기준 소스는 7acd3d02f01cda6910a9e7c062f36611dcac5d37이다. A-2의 같은 잘못된 instance가 전후 안정돼도 기존 credential 수집이 정상 결과를 만들 수 있는 EXAMPLE 재현 요청에서 시작했다. 이번 계약 제공만으로 PLT-01/04 전체나 자격 사용을 완료했다고 집계하지 않는다.

## 공통 함수와 첫 소비 경로

`owned_capture.validate_engine_target(env, target, container, image)`는 I/O 없이 ENV seal·정확 ENV Ref·대상의 engine.container·pin 이미지·소유 run 형식을 대조한다. 다른 ENV나 컨테이너, 누락된 기술 입력, 다른/unpinned 이미지는 `CAPTURE_ENGINE_TARGET_INVALID`로 거부한다. 새로운 기업 허용표나 정책값을 채우지 않는다.

`owned_capture.engine_identity(docker, target, env, container, image, timeout)`는 위 입력 확인 뒤 기존 `identity`를 사용한다. 실제 Config.Image·guard.run·컨테이너 이름/ID·Running·StartedAt와 image ID 형식을 검사한다. 반환은 기존 container_id/image_id/started_at/image 네 값뿐이다. inspect 전체·Config.Env·argv·자격·예외 원문은 반환·저장하지 않는다. 기존 transport가 낸 예외는 호출자가 기존 고정 진단 경계에서 처리한다.

이는 단일 시점 신원이다. 같은 잘못된 owner/image가 전후 안정돼도 각 검사에서 거부한다. 각각 통과한 두 관측의 세대가 달라지면 호출자가 전후 tuple을 비교해야 한다. 실제 daemon/context의 신뢰·worker 귀속·자격 파일 소비·생산자 인증·관측 누락 부재나 전 구간 안정성을 한 번의 inspect로 입증하지 않는다.

기존 lifecycle 입력 검증이 이 공통 입력 함수를 사용하며, 기존 `LIFECYCLE_CAPTURE_INPUT_INVALID` 오류를 유지한다. 제품 `collect_session`은 이 ENV를 고정한 owner callback을 기존 세션 census에 전달한다. census의 전후 신원 비교, 파일/프로세스 재검사, 독립적으로 남긴 앞선 관측과 후속 실패 진단은 유지한다. 센서 asset/profile1~7·CheckDef·PACK31·registry·판정 의미·저장된 EVD/Ref는 바꾸지 않는다.

## A-2 소비 경계

A-2는 자기 엔진 수집기의 새 live 경로에서 동일한 env/target/container/image로 이 함수를 관측 전후 호출하고 비교할 수 있다. 기술 입력이 빠진 과거 target을 자동으로 현재 ENV에 결합하거나, 서로 다른 세대의 자료를 합쳐 PASS/FAIL 근거로 삼아서는 안 된다. 이미 별도 구간에서 결합된 독립 근거와 성공 Ref는 나중 구간 실패로 지우지 않는다. A-2 전용 파일의 소비·새 실행기 버전·회귀는 해당 담당자가 반영한다.

Gateway의 route 자료를 읽는 과거 `route_observations`에는 엔진 container/image 일치를 강제하지 않는다. Gateway 소스 신뢰·유효 변환은 별도 계약이다. Kubernetes는 기존 정확 target/PodUID/containerID/imageID/restart와 `verify_owned_pod` 계약을 유지하며 Docker tuple로 대체하지 않는다.

## 검증

CPU EXAMPLE은 정상 입력, 다른/누락 ENV Ref, 잘못된 pin·container·run, 같은 잘못된 instance의 반복 검사, restart/replacement/image ID 변화, 최소 반환과 비밀 canary 비저장, 제품 세션 callback을 검증한다. 기존 lifecycle·Gateway 수집·Kubernetes·첨부 보존·독립 FAIL/Ref·불변식도 영향에 맞춰 확인한다. 실제 엔진·Docker daemon 기동·이미지 pull·모델 다운로드·AWS 사용은 없다.

