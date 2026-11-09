# GPU 1장: Ready90 실패와 기한 후 Ready 실측

2026-11-09 V9 실행의 실제 기록이다. source `b85dac21bfe380eaa7f06a7fe2b451a2e168daa2`, tree `d1a4d8fa7edecd6b18c3cebc348c318ade9bfc12`, manifest `2e400a46d78465144770afff4de54210cceb5ec8f58539666a11281f83e9126c`를 유지했다. 같은 독립 source V3·venv·고정 모델을 현재 검증해 재사용했다. 이전 mount 기본값 비교 수정 후 preflight·모델 준비·Pod 생성이 실제 통과했다.

## 관측

아래 시각은 UTC다. KST는 9시간을 더한다.

| 관측 | 실제 결과 |
| --- | --- |
| engine Pod 생성 | 11:09:22Z |
| 원 Ready90 만료 | 11:10:52Z |
| 원 wait 실행 | 11:09:23.103610Z~11:10:52.044440Z, 남은 시간 88.896510548초 적용, timeout·returncode -9 |
| 같은 Pod의 Ready 전이 | 11:11:00Z, 생성 후 98초. restartCount 0·같은 UID·observedGeneration 1 |
| 보존 조회 | 11:11:03.161879Z~11:11:03.311074Z에 같은 Pod의 Ready=True 확인 |
| normal control·선정 REQ 수집 | 원 Ready90 실패로 미실행 |
| 제품 EVD·RES·RPT | 생성되지 않음. 따라서 해당 실행의 복원·저장 팩 재판정 대상도 없음 |

원 wait의 시간 초과·프로세스 회수·group terminal·빈 stdout/stderr·원 오류를 보존했다. 로그의 모델 적재·엔진 초기화·API 시작과 이후 health 200 두 줄을 확인했다. health 줄 자체에는 시각이 없어 앞선 로그 시각을 대입하지 않았다. 기한 후 Ready를 90초 조건 충족이나 정상 추론 응답으로 집계하지 않는다.

## 정리·비용

원 namespace UID/RV 조건으로 삭제했고 11:11:55.039431Z에 NotFound·GPU process 0을 기록했다. execute 프로세스는 11:16:44.777170Z에 exit 1·reaped·group terminal·cleanup_errors=[]로 종료했다. 원 stop180 관측에서는 final이 없었으며 이 실패를 유지한다.

후속 원 조회 11:22:48.588703Z~11:22:49.574585Z에서 소유 서울 EC2 3대 stopped·공인 IP 0·활성 SSM 0을 확인했다. 해당 시각 서울 실행 장부의 보수 추정은 **$74.8135829332048**이다. 확정 청구나 크레딧 차감 결과가 아니며 EBS 440GiB 저장비는 계속된다. $100 예산에서 예비 $5를 제외한 잔여는 $20.1864170667952로 기록했다.

## 원자료와 검토

공개 저장소에는 원 요청 본문·Secret·원격 자격 증명을 추가하지 않는다. 원자료는 세션2의 `out/one-gpu-req-sup16-join-20261109-v1/`에 보존했다.

| 자료 | bytes / SHA-256 |
| --- | --- |
| `actual-ready-timeout-main-v9.zip` | 477405 / `13f6af7b27b801191da5e0eb88dd490c06c993149903a2169264fe361d0c5399` |
| `actual-ready90-control-originals-v9.zip` | 2523083 / `33f19faa6cb02fe384abca92e4bf63821f91fbf2b935a8f47a1daa4a9decb038` |
| `required-ready90-lateReady-stopped-cost-control-link-v9.json` | 1966 / `438e362f536b1ee2a11ae5937e8839d0aed6fcdbeaabec6ba469226904cd072e` |

세션6의 main 의미 대조 595개·최종 control 무결성 대조 92개·Ready90/late Ready/정지/비용 대조 189개·정확 최종 요청 결합 14개에서 불일치 0을 확인했다. 각각의 범위가 다르며 시험 통과 개수나 제품 PASS 수로 합산하지 않는다. 원 pending 기록은 수정하지 않고 위 required link로 후속 해소했다.

## 다음 별도 운영 조건

실제 요청 검증을 계속하기 위해 같은 source·모델·기동 명령에서 **실제 Pod Ready와 직접 HTTP `/health` 정상 응답을 확인한 뒤 정상 대조·REQ-08·09·10**을 실행하는 별도 운영 분기를 준비한다. 초기 대기는 원 Pod 생성 기준 600초의 잔여와 원 전체 관측 1800초의 잔여 중 작은 값을 사용한다. 같은 deadline을 요청 종료까지 유지하고 보호 타이머를 재설정하지 않는다. 원 Ready90 충족 여부는 별도 성능 관측으로 남긴다. 원 정책·판정·수락 기준을 바꾸는 처리가 아니다.

실행 전 검토에서 HTTP health 확인이 첫 normal POST보다 앞에 연결되지 않은 누락을 발견했다. 이 순서와 원 실패 전파만 보완하고 새 고정본을 검토한다. 이 문서 시점에 다음 분기의 실제 요청·제품 Ref 완료를 주장하지 않는다. SUP-16 순차 역할 실측과 두 live target을 요구하는 전체 paired factory도 별도 상태다.
