# SUP-16 전용 디렉터리·읽기 전송 변경 범위

R7의 실제 API preflight는 성공했지만 첫 capture가 일반 출력 디렉터리 검사에서 실패했고, 큰 archive 회수도 원 기한에 걸렸다. [실제 운영 관측](SUP16_ACTUAL_OBSERVATIONS_20261111.md)에 원 실패·부분 회수·정리 기록을 보존한다. 아래 변경은 이 두 연결 경계를 보수한 준비 범위다.

## 전용 디렉터리

[생성 함수](../../../tools/gpu_b_client_private_root.py)는 기존 UID1000/mode0700 ASSETS 아래에 정확한 세 역할의 새 디렉터리를 exclusive 생성한다. root 설치기는 새 자식만 UID1000으로 지정한다. 기존 경로·일반 capture output·A 코드의 권한을 고치지 않는다. 실제 소비 후보는 `--client-private-root`로 받은 동일 경로에 wire와 첫 실패 진단을 두며 기존 FD·소스·기한 검사를 유지한다. 공개 생성 함수만으로 helper/adapter/resolver·선정 입력·배포 연결이 완료됐다고 표시하지 않는다.

실제 A `reserve_output` 함수에서 시작하는 새 CPU 계약은 S2 생성 함수와 helper 소비를 연결했다. CPU에서 명시한 일반 output 0775·0700 두 경우의 기존 권한·inode·bytes를 보존했고, 잘못된 private 0775는 거부했다. R7의 실제 output UID/mode를 이 fixture로 추정하지 않는다. 해당 계약의 binding·일부 dependency와 원 rc23 재생 범위를 실제 전체 constructor/API/GPU 성공으로 확대하지 않는다.

별도 새 root CPU 계약은 같은 생성 함수의 root→새 자식 fchown1000/1000·0700 분기와 기존 부모·marker 보존, 기존 자식 재생성 거부를 확인했다. 이는 이전 UID1000 CPU의 미실행 분기를 보완한 것으로 두 계약을 전체 root 설치기→역할 통합 실측으로 합산하지 않는다. 추가 private 원자료의 archive 보존 호출선은 별도 선정 코드와 원자료로 검증한다.

## 읽기 전송

[읽기 child](../../../tools/gpu_b_readonly_ssm_invocation.py)는 정확한 CommandId·선정 InstanceId의 `get_command_invocation` 하나만 호출한다. 요청 파일의 UID1000/REG0600/nlink1·FD identity·SHA를 확인하고 실제 SDK 응답과 오류 metadata를 보존한다. 명령이 Failed라면 그 상태·반환코드를 바꾸지 않는다. 원 부모는 검증한 child bytes를 실행하고 65초 제한·미종료 명령 차단을 유지한다. 기동·정지·send_command와 다른 API는 기존 CLI를 사용한다.

SDK의 connect10/read45와 `total_max_attempts=1`을 고정한다. total은 초기 요청을 포함하므로 재시도를 추가하지 않는 값이다. [공식 Config 문서](https://docs.aws.amazon.com/botocore/latest/reference/config.html), [SSM API 계약](https://docs.aws.amazon.com/boto3/latest/reference/services/ssm/client/get_command_invocation.html)을 대조했다. SDK는 운영 환경에 이미 설치된 패키지를 사용하며 이 공개 도구가 저장소의 기본 실행 의존성을 추가하지 않는다.

새 child CPU 한 계약의 7개 변형은 실제 Linux UID1000 요청 파일→정확한 main→명시 SDK 대역이다. 부모 CPU 한 계약의 4개 변형은 실제 source/request FD→명시 subprocess 대역이며, 그 증거는 도구 출력 복사본이다. SDK child와 부모의 대역 범위, 원 Failed/1·metadata·InvocationDoesNotExist·timeout raw·barrier·잘못된 권한 거부를 구분했다. 기존 core/namespace/전체 suite를 반복하지 않았다.

2026-11-11 04:05 KST에는 GPU를 기동하지 않고 기존 CloudShell Python3.13/SDK에서 이전 정확 terminal CID를 한 번 조회했다. 원 응답의 HTTP200·RetryAttempts0·Success/0을 약 0.993초에 받았고 원 metadata와 요청·응답 SHA·terminal 연결을 대조했다. 새 mutation·동일 CID 자동 재조회는 없다. 단일 읽기 성공은 GET4/900초 전체 archive 회수나 모델·세 역할 성공의 증거가 아니다.

## 고정 소스·검토

| 범위 | 원 SHA256 |
| --- | --- |
| 공개 생성 함수 3,089B | `d8d73be080c66edbe5d353598830951914b13f8b028c3a447f439c121ead63e3` |
| 공개 읽기 child 4,587B | `7a057bca14ab3f164414d8b8aa2c737d041550ba168b55f4d004c3bc29684656` |
| child 독립 검토 | `a1c6a01b5a7b76c129c5698a9f1618bd5bf9bec5ed0d9aa232be494c3ea119a0` |
| 부모 읽기 변경 독립 검토 | `0006610ec51872e93bd26acfbb67557b50b73691b008a76e85a1b5b744cb1a84` |
| 전용 생성·소비·root 분기 독립 검토 | `771d673cae1a0a3b6a93fcd4c6cd52550ac65ef88519a31bce37c57ca6fa0247` |
| 실제 SDK 13개 원자료 대조 | `7c88366dbace2a5047c552f0486f256ca0f76b0bff4df8b67884b6f95f22de51` |

검토 수는 제품 시험 통과 수가 아니다. 이 B 운영 도구는 보호 제품528파일과 별도 범위이며 실제 runtime 원 `2cb01e9f`/466파일·선정 배포 묶음도 구분한다. PACK33 DRAFT·LAB10/33·FAIL/UNKNOWN/정확 Ref를 변경하지 않는다.
