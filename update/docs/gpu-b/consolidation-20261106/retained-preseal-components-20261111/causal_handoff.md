# SUP-16 GB12 원자료 인과 분석 v1

전체 회수·무결성은 통과했지만 원 SUP-16 질문은 미완료다. 최초 역할 실패는 challenge-partial의 후보0 guard이며, 그 앞의 seal transport timeout도 별도로 남는다. 설치 실패 원인은 미확정이다.

| 실제 phase | 기록/파일/후보 | seal | publish 종료→capture 시작 | 원 observe 오류 |
|---|---|---|---|---|
| normal-before | 3/3/1 | true | 94.751154초 | 없음 |
| challenge-partial | 0/0/0 | false·gap | 1.738981초 | 후보1 요구 guard |
| normal-recovered | 3/3/1 | true | 72.059217초 | 없음 |

정상 전후의 PID98은 전체 FD 250500968B와 기준 SHA 일치를 관측했다. 동시에 두 journal 모두 END=BOUND_REACHED이고 root operation pair/소진 완료는 false다. 정상 후보1·seal 성공을 모델 적용/제품 PASS로 확대하지 않는다. 두 local boot-record는 normal/appliedfalse/worker_consumption_claimedfalse이며 공식 EVD/RES/RPT가 아니다. challenge는 writer 전에 중단했다. Store/DB 복원·POL 재판정 없이 old FAIL·UNKNOWN·Ref를 보존한다.

원 CheckDef SUP-16은 부분 산출물 소비와 안전 거부/관측 부재를 구분한다. 원 collector는 challenge의 실제 mismatch를 violation으로, boot/native 미확보를 missing으로 구성한다. 지금은 부분파일을 엔진이 안전하게 거부했다는 native 로그/기록이 없다. 후보0·빈 로그·수집 timeout을 의도된 거부 성공으로 치환할 수 없다.

lazy Writer는 install 성공만으로 journal을 만들지 않는다. challenge의 install 호출/반환·첫 hook/emit·Writer open/write 오류는 원 전체 자료에도 없다. 나중 SEALED=true나 preparation probe 성공으로 이를 채울 수 없다. 원 빈-session sealer의 최대4초 census와 해당 실패 root capture잔여3.625705922초의 상호작용은 정적 가능성으로만 남긴다. 원격 실행 도달/실행시간 관측 없이 실제 유일 원인으로 확정하지 않는다.

최소 B 보강은 같은 기존 reader의 snapshot→seal 사이 inventory 원반환/오류를 receipt.before_seal에 보존하는 f868 후보이다. 선행 SEALED/파일/PID-start/boot/FD 정보를 얻으며, 원 timer·후보 요구·worker·A 소스·제품 판단은 바꾸지 않는다. 과거 R8 관측은 추가하지 않는다.

A 단일작성 경계는 원2cb의 collectors/lifecycle.py54–66, adapters/vllm_file_read_sensor.py221–228, adapters/vllm_lifecycle_sensor.py393–397 및1032–1052이다. sitecustomize 반환 누락, install False와 원 예외 흡수, 첫 emit/Writer 오류를 같은 실제 엔진 PID/start/boot/import origin에 결합할 bounded 진단 반환이 필요하다. S5는 공통 코드를 수정하지 않았다. 원 상한을 결과에 맞춰 늘리는 요청도 아니다.

정확 member·SHA·Ref·원UTC/monotonic 분리·S4 검증 재사용·최소 후속 조건은 causal-findings-v1.json에 있다. 원 source2cb01e9f/manifestac423 및 R8 boot78fd6444를 회수 세대나 현재 공개 코드로 대체하지 않았다. 전체 CRC 재검·압축 코드 실행·새 GPU/AWS는0이다. 새 실제 실행은 세션2가 검토된 새 패킷과 실제 ENVtarget/요청 신원을 고정한 뒤에만 연결한다.
