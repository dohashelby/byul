# 실제 preflight·모델 준비 통과와 Pod 기본값 검사 수정

v8은 실제 설치된 V3 b85 source와 venv를 재사용했다. source-install 단계와 재설치 호출은 제거했고, 원 preflight의 현재 HEAD·tree·2,160개 tracked blob·clean 상태·manifest466·isolated import origin 검증은 유지했다. 실제 source는 `b85dac21bfe380eaa7f06a7fe2b451a2e168daa2`, tree `d1a4d8fa7edecd6b18c3cebc348c318ade9bfc12`, manifest `2e400a46d78465144770afff4de54210cceb5ec8f58539666a11281f83e9126c`다.

## 실제 도달 범위

| 2026-11-09 KST | 실제 결과 |
| --- | --- |
| 19:34:42 | 보수 장부의 start-intent anchor. 뒤이은 실제 CLI 수락은19:34:53이며 두 시각을 구분 |
| 19:36:58 | 새 boot의 L4 1장·CPU8 health와 원2시간 보호 타이머 활성 확인 |
| 19:37:28~32 | 실제 현재 source·실행 환경 preflight Success0 |
| 19:37:41~44 | 실제 모델 준비 Success0. 고정 모델7파일과3영수증의 FD·UID1000·mode400·nlink1 확인, 원 변환·입력 수정0 |
| 19:37:51~52 | observe의 before-ready `own_pod`에서 `SUP03_ACTUAL_POD_MOUNT_CHANGED`로 중단 |
| 19:38 | 원 preserve도 같은 검사에서 실패. 일부 원자료 보존 한계 유지. engine은 이후 Running이지만 ReadyFalse였음 |
| 19:38:57 | 정확한 namespace UID/RV 정리·NotFound 빈 응답·정리 후 GPU 프로세스0 확인 |
| 19:40:44 | 원 정상 stop-intent anchor. 실제 CLI 수락은19:40:48로 별도 보존 |
| 19:43:50 | 원 execute327 exit1·reaped·group terminal·cleanup 오류0. 원 stop180 미관측과 supervisor final 부재 유지 |
| 19:45:07~08 | full7에서는 대상 stopping, 활성 SSM0·EIP0·EBS440GiB |
| 19:48:37 | 별도 EC2-only에서 소유3대 stopped·공인 IP0. 정확한 off 전이 시각은 미확인 |

vLLM Ready 대기·정상 요청·REQ-08·09·10 제품 수집에는 들어가지 못했다. 새 EVD/RES/RPT와 복원·재판정 대상은 없다. 호스트와 모델 준비 성공을 엔진 Ready나 제품 PASS로 집계하지 않는다.

## 실제 Pod 응답의 단일 표현 차이

제출한 engine의 `/guard-lifecycle/data` 마운트에는 `readOnly:false`가 있고, 실제 Pod 응답에서는 그 키가 없었다. 이름·mountPath와 나머지 예상 키는 같았다. 생성·observe·preserve의 같은 Pod UID 원 응답 세 개에서 이 차이를 확인했다. 기존 `mounted.get('readOnly')`는 키 부재를 `None`으로 읽어 `False`와 다르다고 판정했다.

[Kubernetes Pod API의 VolumeMount 정의](https://kubernetes.io/docs/reference/kubernetes-api/core/pod-v1/#volumemount)는 `readOnly`의 false·미지정을 같은 기본값으로 정의한다. 이는 API 필드의 의미이며, 이번 기록이 실제 파일 쓰기 권한을 따로 실측했다는 뜻은 아니다.

후속 수정은 `readOnly`에만 정확한 bool 검사와 부재 시 false 기본값을 적용한다. 원 실제 Pod JSON을 기존 검사에 소비하면 실패하고 수정 검사에서는 통과했다. 합성 음성4건은 예상 false를 true로 바꾸기, 원 읽기 전용 모델 마운트의 키 누락, 비bool0, subPath 변경이며 모두 원 오류로 거부됐다. 총6건의 CPU 연결 검증으로, 실제 다음 GPU 실행·Ready 성공은 아니다. 다른 mount·hostPath·UID·run 검사와 원 기한은 유지한다. 원자료와 기존 오류는 수정하지 않는다.

구체 원인 검토 SHA는 `5fec2ac63efae37eb855552158d0e0b6fc7635bee27f589e2e0413a24e390e8c`, 원 main 의미 검토 SHA는 `7d84b0f0a4b4312d9ee8ace417a66d95e0677a24c2070cc91d57146ba5b4ed78`다.

## 원자료와 비용 마감

main ZIP은 470,019bytes·211members(원자료210개), SHA-256 `58ff06929ac6a04a4f2a506b84c4a15e188b4365a46d6ce0c6d5e8010c9f58bf`다. 원 control ZIP은 2,471,412bytes·1,189members(원자료1,188개와 manifest), SHA-256 `4b05dd2b509863f84fcdd6c18b8b316127a683b95cd63976b842203a5975d7ce`다. 로컬 CRC·전체 길이/SHA·목록의 중복·누락을 대조해 불일치0이며, nested 과거 검증은 반복하지 않았다. 원 수락 기록은 start1·SSM58·stop1이고 미해결 제어 작업0이다.

최종 독립 검토의 control·정지·비용209개 대조 불일치0, SHA-256 `79f3b74df66c441d701f541c3eb7be94b3341e19ef4efbd984c04d62c64e29f7`로 마감했다. 이전 초기·main 검토와 원 stop180 실패를 분리해 연결한다.

서울 실행 장부의 19:48:37 마감 기준 누적 보수 추정은 **$74.46522181803866**이다. 확정 청구가 아니며 원 start-intent부터 후속 stopped 관측까지 보수적으로 compute/IP 구간을 닫았다. EBS440GiB의 $0.055748/h 저장 비용은 계속된다. 다음 실행은 같은 source·venv·모델을 현재 검증해 재사용하고, 검토한 Pod 기본값 처리만 적용한다. SUP-16 별도 역할 관측 준비는 병행하며 전체 paired factory와 구분한다.
