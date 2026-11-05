# SUP-04 실제 모델 CPU 준비

전용 `tools.gpu_b_sup04_realmodel`은 기존 REQ-07의 고정 CPU image/model/readonly template 및 `cpu-bounded` 설정을 재사용한다. 새 공통 실행기나 AWS 운영 기능을 만들지 않는다. 기본 실행은 소스·manifest·모델 inventory·ENV·두 DRAFT 정책·실제 registry factory의 입력 검증만 하며 엔진을 만들지 않는다. `--execute`는 세션2가 실행한다.

실제 `sandbox.up` target은 ENV ref가 없으므로 원 target을 수정하지 않고 실제 Docker 전후 snapshot·소유 label·RepoDigest와 sealed ENV를 확인하여 별도 `bound-target.json`을 쓴다. 다른 ENV ref·교체·불일치·누락은 거부한다. 기존 collector가 실제 수집 시 이미지 신원을 다시 관측한다. 과거 Python stub의 501 결과는 새 실제 vLLM 모델 결과와 구분한다.

한 실제 모델 세대에서 승인 digest 포함과 명시적 빈 목록을 순서대로 실행한다. 각각 별도 DRAFT/EXAMPLE POL·factory·EVD/RES/RPT·DB·archive를 만들고 별도 DB 복원 및 원 시각 저장팩 재판정을 확인한다. 포함은 부분 범위의 UNKNOWN을, 빈 목록은 독립 FAIL을 보존한다. 정상 추론 결과도 실제 원문대로 저장하며 후속/정리 오류로 앞선 판정을 지우지 않는다. 전체 SUP-04 완료나 GPU 실측으로 세지 않는다.

구체적인 고정 commit·자산·명령·검증은 공유 `prepare/SUP04-cpu/v2/packet.md`를 참조한다. 원 CPU ready600초·container_config 요청30초·소유 cleanup30초를 유지하며 시험 중 기한을 늘리지 않는다. 키는 메모리로만 전달한다. 출력은 기존 소스·자산·모델·실행 디렉터리 밖의 새 경로여야 한다.
