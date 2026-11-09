# GPU 1장: 실제 Ready와 isolated target 연결 오류

2026-11-09 V11 실행은 고정 source b85·source V3·기존 venv·모델을 재사용했다. 원 Ready90 실패는 유지하고, 새 운영 분기에서 실제 Ready 뒤 직접 HTTP health→정상 대조→선정 REQ 수집 순서를 고정했다. 초기 대기와 후속 요청은 같은 원 Pod600·전체1800 잔여를 소비하며 보호 타이머는 재설정하지 않았다.

## 실제 결과

| 단계 | 원자료에서 확인한 결과 |
| --- | --- |
| source·preflight·모델 준비 | 정확 b85/tree·2160파일·현재 import origin과 모델/영수증 FD를 검증하고 통과 |
| engine Ready | 11:58:41Z 생성→12:00:18Z 전이, **97초**. 원90초 성능 조건은 false, 별도600초 진행 조건은 true |
| 실제 입력 수집·ENV seal | 현재 namespace/engine/probe 원 JSON과 EXAMPLE·UNVERIFIED 입력 ENV를 생성 |
| target 결합 | `bind_req_target.py`의 `from tools.gpu_a_run_inputs import _binding`에서 `ModuleNotFoundError: No module named 'tools'`로 exit1 |
| HTTP health·정상 추론·REQ factory | target 생성 전 중단으로 미실행 |
| 제품 EVD/RES/RPT | 생성0. 위 입력 ENV 생성과 구분하며 복원·저장 팩 재판정의 제품 대상도 없음 |

시각은 UTC다. 이 오류는 UID1000·`env -i`·source V3 표준 venv의 실제 `-I -B` 명령에서 발생했다. `guard` 설치는 검증됐으나 비패키지 `tools` namespace를 격리 CLI에서 가져오는 접점을 놓쳤다. 모델·센서·제품 판정 FAIL로 승격하지 않는다. 기존 검토에서 고정 자산 핀을 재사용하면서 이 import 경계를 확인하지 못한 누락도 후속 검토에 남겼다. 과거 검토·원 오류는 수정하지 않았다.

## 정리와 비용 마감

12:01:22.755203Z에 원 namespace UID/RV 삭제·NotFound·GPU process0을 확인했다. 원 driver3173은12:06:20.571872Z에 exit1·reaped·group terminal·cleanup_errors=[]로 종료했다. stop은1회이며 원180초 관측 실패와 supervisor final 부재를 보존했다.

원 full 조회12:07:20.479704Z~12:07:21.346831Z에서는 대상이 stopping/IP1이었고,12:09:08Z의 EC2 전용 조회에서도 stopping이었다. 마지막 EC2 전용 조회12:10:41.448651Z~12:10:41.874316Z에서 소유3대 stopped/IP0을 확인했다. 이 마지막 조회를 새 full 조회로 바꾸어 쓰거나 정확 off 시각으로 표시하지 않는다.

마지막 관측까지 보수적으로 누적한 서울 실행 장부 추정은 **$75.16934681084811**, $100 예산에서 예비 $5를 제외한 잔여는 **$19.83065318915189**이다. compute/IP 누적은 닫았고 EBS440GiB 저장비는 계속된다. 크레딧 차감·확정 청구 결과가 아니다.

## 근거

| 세션2 보존 자료 | bytes / SHA-256 |
| --- | --- |
| `actual-target-bind-failure-main-v11.zip` | 514603 / `3d699a01bb2adf5ef9a409b1676d6ee39b2c8954c23ce8d2d8174da1714bdc41` |
| `actual-target-bind-failure-control-originals-v11.zip` | 2637773 / `0723ae18101d066b5d40500fc3728a79296b08478e658e4b638dda8c55f33b6b` |

main 원자료259개 member/258개 payload와 control1310개 member/1309개 payload의 첫 CRC·해시 대조를 통과했다. 세션6 main 의미 대조762개·최종 정지/비용 대조242개에서 불일치0을 확인했다. start1·SSM61 CID·stop1, archive 생산자와 반출 GET45개의 원 연결을 보존했다. 이는 제품 시험 통과 수가 아니다.

- main 의미 proof: `1bffc61c1632363c93ffe338e1756c51913898614346e9aa43931db7f8dc6c3a`.
- 최종 정지·비용 proof: `2ccaded7eff0a37155b6a1a816b9f088f0326bf5c24323cb9ace8d3b53ce2839`.

## 최소 수정과 CPU 검증

원 입력·검증·source·venv를 유지하며 b85의 원 `_binding` 함수892bytes를 운영 자산에 그대로 고정했다. 원 `tools` import1개를 제거하고 원 validator import·출처 상수만 추가했다. read/main AST와 나머지 build 검증은 동일하다. 원 guard의 소유·모델 mapping·HTTP target 검증을 제거하지 않았다.

고정 자산은 [gpu_b_bind_req_target.py](../../../tools/gpu_b_bind_req_target.py)이며5338bytes/SHA `28b101d2bedf24dd3f43151df2b2b9d00597b3635102e439adfe0a4916bd720a`이다. 원bytes를 그대로 보존한 운영 자산이며 검증 의존 환경은 source b85의 guard다. 최신 main 통합본 전체의 runtime 검증으로 표시하지 않는다. 도구 파일명에는 상대 의존 경로가 없으며 실제 실행 입력·생성 target·자격 증명은 공개하지 않는다.

보존된 실제 입력5개와 원018 argv를 사용한 UID1000·CPython3.12.3·`-I -B` CPU 계약1개가 통과했다. tools namespace가 없는 조건에서 main 정상 처리와 원 run ID 거부를 확인하고, 실제 로드된 guard16파일이 b85 Git 객체와 pre/post 일치함을 확인했다. 두 consumer 구간의 부모 시간은3.5775433초이며 입력 추출 준비 시간은 제외한다. 별도 WSL venv에서 삭제된 Pod의 입력을 오프라인 소비한 범위로, EC2 venv 전체 동일성·새 live target·HTTP health·GPU 제품 수집을 의미하지 않는다.

독립 수정 검토208개 확인의 불일치·구체 결함은0이며 proof SHA는 `6f9c023d22746ef9a561705f07cfbe98ee75bc1affe8a441d6548714680c8d67`이다. 최초 추출 파일명 오류는 main 실행 전에 중단됐고 원 경로만 정정했으며 이를 실제 CPU 재시험으로 집계하지 않았다. 다음 실제 GPU 결과는 별도로 기록한다. SUP-16 순차 역할 실측과 전체 paired factory도 이 실행에서는 완료하지 않았다.
