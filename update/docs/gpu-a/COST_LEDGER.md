# 담당 A GPU 비용 장부

GPU 서버를 켤 때마다 한 줄씩 남긴다. 단가는 출처(요금표 날짜나 청구서)를 적고, 확인하지 않은 값은 "추정"으로 표시한다. 다중 GPU는 구성·비용·정리 계획을 먼저 승인받고 승인 기록 위치를 적는다.

| 날짜 | 시작~종료(KST) | 리전 | 인스턴스 유형 | GPU 장수 | 가동 시간 | 시간당 단가(출처) | 비용 | 목적(점검 ID·증거 묶음) | 정리 확인 |
|---|---|---|---|---|---|---|---|---|---|
| 2026-11-03 | 19:28:19~21:47:45 중지 요청 / 21:57:10 중지 관측 | ap-northeast-2 | g6.2xlarge On-Demand | 1 | 약 2시간19분~2시간29분(관측 범위) | $1.20208/h(인계 README, 2026-10-26 자료; 현행 청구 단가 미확인) | 추정 $2.79~$2.98(EBS 제외, 실제 청구 미확인) | 인계 01~03, G-A1·A2·A3·A4, native 구성요소 후속, Linux 전체 회귀. 24개 Store와 원자료 반출 완료 | 엔진 scale 0, 시험 예외·probe·native Pod 정리, GPU 0MiB. EC2 stopped 확인·자동 공인IP 해제. EBS gp3 300GiB 보존(지속 비용 별도), 볼륨·스냅샷 삭제 없음. Claude 자동 계속 꺼짐 |
| 2026-11-05 | 17:59 시작(i-04f510eaa76c2bc89)~19:14 중지 요청 / 20:12 stopped 관측(그 사이 마지막 stopping 관측 19:28) | ap-northeast-2 | g6.2xlarge On-Demand | 1 | 약 1시간15분 | $1.20208/h(콘솔 시작 화면 표시 Linux 온디맨드 단가, 2026-11-05) | 추정 약 $1.50(EBS 제외, 실제 청구 미확인) | 이전 인스턴스는 사용자가 삭제. 새 인스턴스에서 인계 01~03 재실행, 2단계 P1·P2 생산자 GPU 확인(`v1-producers`·`v1-producers-kv`, 세션 a-p1-20261105-1~5), Linux 전체 회귀. 원자료 반출 완료(`byul-a-20261105.tgz`, SHA256 `8bb4682d…e95d`) | 엔진 scale 0, 시험 예외·probe 정리, GPU 0MiB. 예외 회수 뒤 시험 Pod의 엔진 `/health` 시간 초과 확인. 중지 요청 뒤 SSH 접속 불가 확인. EC2 stopped 확인·자동 공인IP 해제. EBS gp3 300GiB·키 페어·보안 그룹 `byul-dev1-gpu-ssh` 보존 |

| 2026-11-05 | 21:11:44 재시작~진행 중 | ap-northeast-2 | g6.2xlarge On-Demand | 1 | 진행 중 | $1.20208/h(EC2 서울 인스턴스 유형 콘솔, 2026-11-05 재확인) | 종료 후 산정. 사용자 승인 EC2 상한 $8, 보존 EBS 별도 | process-device-provider·P2 V2 runner GPU 확인과 A 담당 단일 GPU 실측. 고정 소스 25cb08f, 원자료 byul-dev1-evidence/20261105-codex-takeover | 6시간 뒤 host poweroff 예약 확인(2026-11-06 03:12:17 KST). SSH 원본 IP/32 유지, 새 공인 IP 확보. 증거 반출·engine scale 0·EC2 stopped 최종 확인은 실행 후 기록 |

정리 확인 칸에는 인스턴스 중지·삭제, 볼륨·스냅샷·공인 IP 정리 여부와 자동 중지 설정을 적는다.
