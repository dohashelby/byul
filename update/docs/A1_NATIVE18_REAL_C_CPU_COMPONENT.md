# native18 실제 C·CPU 구성요소 검증

사람이 승인한 고정 실행안에 따라 전용 WSL 환경에 Torch2.13.0+cpu·safetensors0.8.0 및 고정 전이9개를 설치했다. 원 source는 ecbbcaccb06a0f4cf88e75c09e53a7e878d19bb4, 통합 전달은 d5ed533bd49d6c7f82a22ffdbf0fd842de560812, 원 worker SHA는3498c6733cce7e46282c5a08e6978245dd8d98bec7ae35d17d6919e0f68de9b5다. 대상10파일은 현재 f9304d2 소스와 bytes가 같다. 이 관계의 범위는 그10파일이며 현재 전체 suite/serving·배포 image·worker 검증으로 소급하지 않는다.

실제 실행은 safetensors C 클래스/descriptor·고정 package3파일과 Torch CPU, 공개 합성 파일, 자기 프로세스의 제한된 mapping metadata다. vLLM module world/model/parameter callback·ENV/target/POL은 EXAMPLE이다. 원 metadata parser가 투영했고 모든 truth·process/worker binding·승인모델·최종tensor적용·mapping 해제는 미확인/false로 유지했다. 함수 반환·파일 선택·hash·관측후 map 부재를 최종 소비/해제로 승격하지 않는다.

검증한6계약은 default None의 C 경로, 명시 lazy, generator 부분 close, 유효 callback1개 뒤 정제 오류, 잘못된 공개 합성 header의 원 native 오류, package alias 교체 시 관측자격 LIBRARY_UNAVAILABLE다. 정상/부분close의 C OPEN/ENTER/TENSOR/EXIT, 후기 OSError와 invalid header의 SafetensorError kind를 원 parser 결과에 보존했다. alias 대조에서도 원 C동작은 이어지며 관측자격은 낮아진다. 실제 모든 rank/model·정책·상위READY/서비스와 인과 결합은 미연결이다.

첫6사례 프로세스는5개 receipt를 남기고 마지막 alias대조에서 실패했다. 합성 모듈을 FunctionType으로 재구성할 때 __builtins__ 전역이 없어서 raw C 경로에 KeyError가 났다. 관측기 없는 원 C 직접대조2건은 tensor2개를 읽었다. 원실패와5receipt·원worker를 유지하고 합성모듈에 정상 Python namespace만 보완했다. 원 assert AST 전체를 그대로 유지한 별도 영향 worker68eb84ec4767fa565442be25f86649d23aaeaa69c7903e8959935e613ed76fbd로 alias1사례를 재검증해 LIBRARY_UNAVAILABLE/예외없음을 확인했다. 최종 근거는 원5+영향1이며 최초6사례 실행 전체 통과로 표기하지 않는다. guard source·source/image/lab gate·판정/skip은 바꾸지 않았다.

다운로드는 원8wheel 재사용까지 누적202775336bytes(상한262144000), 전용디스크958570431bytes(상한3221225472)다. 설치전 setuptools의 동봉12개 metadata를 최상위 배포판으로 센 수집기 가정을 보수했고 최초실패를 보존했다. WSL uv 부재로 새venv의 내장pip가 같은 wheelSHA/오프라인/no-deps/binary-only 제약을 적용했다. 정확11패키지와 bundled pip24.0만 설치 metadata에 있다. 재다운로드/재설치 없이 영향1사례를 확인했고 원1407소스는 전후 동일하다. 프로젝트 pyproject/uv.lock·기존환경 변경 명령과 AWS/IAM/GPU/Start는0이다. 전용env·wheel·정제 증거는 승인 경로에 보존한다.

원 증거는 외부 인계의 native18-real-C-CPU-verified/original-component-evidence.zip(185100bytes, SHA435bfdbb726a8b6f7a093ce7ef4083acfb151a6489ed227912d797356bb8a3b4)에 있다.6개 receipt, 원/영향 worker·scope·source전후 hash·설치/다운로드/실패·대조 기록을 원bytes로 반출했으며 wheel/venv 바이너리는 묶지 않았다. 임의 예외 원문·실제 tensor 값·raw maps/주소·요청/응답·환경/argv를 저장하지 않았다.

이 문서 커밋은 runtime/팩/시험/의존성 설정을 바꾸지 않는다. 기존 source/wheel/설치279파일·A/B common387의 byte 동일성을 재사용하고 정확 HEAD source gate와 CI는 별도 기록한다. original GPU창 종료·추가Start0·총46.66USD/과거사용량·보존120GiB 비용과 부모A39/56/4·A1 19/27/2를 유지한다. 실제 approval/inspection 생산자·worker/model 귀속·정책·fresh ENV/대상과 GPU 시간/소유권·복구 책임은 별도 미확정이다. 원목표와 OBS02 전체 분기는 미완료다.
