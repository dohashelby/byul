# 실제 native ID와 private 준비 경계 수정

V12 실제 정상 요청은 성공했지만 제품 준비 디렉터리 조건에서 중단됐다. 정상 원 응답의16hex와 B capture의32hex 가정도 달랐다. 기존 실패·UNKNOWN·Ref를 보존하고 다음 두 연결만 보완했다. 정책·판정·요청 body/headers·HMAC·completion의 `-0` 규칙은 변경하지 않았다.

| 수정 | 정확 범위와 검증 |
| --- | --- |
| native 응답 ID | 고정 db952 `random_uuid()`는 UUID 하위64비트를16자리 소문자hex로 만든다. 빈 headers/custom ID 없음·단일 prompt/n=1·고정 V1 모드에서 B regex를32→16으로 정정했다.16/32를 함께 허용하거나 suffix를 탐색하지 않는다. |
| 테스트 입력 | 고정 실제 생성 함수의 AST를 사용하는 fixture로 원 가정을 교정했다. 기존 검증 의미와 duplicate/truncation/suffix 거부·독립 FAIL·정확 Ref 복원을 유지했다. |
| callback 준비 | 원 producer 호출 직전에 자식 프로세스의 `os.umask(0o077)`을 명시했다. 원 producer/엄격0700 consumer·옛 디렉터리는 수정하지 않았다. 새 출력의 원 `OUTPUT_EXISTS` 거부도 유지했다. |

## 고정 source와 검증

- B 수정: `7765b6f632d85216f4637cb92c57d3d1a61f4cbf`, parent b85. 실행 로직 변경은 regex1곳이다.
- 실제 다음 GPU source: [2cb01e9f](https://github.com/dohashelby/byul/commit/2cb01e9f08f9a123b8fffff00f9e798060115db9), tree `912cb0924559343af939b5b525e865590a91795c`. 원 목록 재생성1줄만 추가했고466파일 manifest는 `ac423c8243af7b824bd66f7dadc5bd4ef5d827026a86d66f0c32dd28f3b4cf8b`이다. 전체 tracked2163파일이며 이전2157blob과 변경6blob을 구분한다.
- PR의 최신 main 통합:509파일 manifest `b74b04353e0664de7c2c6289883e094b813884289ea7c86a3619d2eaa8402b01`.3ff0 대비 capture 해시1줄만 변경했고 B 실행 파일 SHA `2dec5d3d7ebc506c43e016ba9e1d3270ad42406f0de2974892c3d54ac8f85185`는 GPU source와 동일하다. 나머지508파일·pack_ref/paths·기존 검토 기록은 보존했다.

신규15+기존 직접17, 총32개 CPU 사례가 통과했다(pytest105.10초/부모107.818914898초). 현재 main 문맥에서도 같은32개를 통합 회귀해 통과했다(pytest106.98초/부모108.922초/전후6파일 불변). 이를 신규64개로 합산하지 않는다. 원 raw FAIL·EXAMPLE/ABORTED·3축 UNKNOWN·null·공유 RPT official=false와 정확 Ref는 유지했다.

독립 source/CPU 검토207확인·CPU ZIP 첫 무결성200확인의 불일치·구체 결함은0이다. source proof SHA `38166186fca174a4fbba6ed433178a38c851a28a22ce93d026ab962bfaa7161d`. PR509 문맥 검토49확인의 불일치는0이며 정확b74에 실제 검토자·시각 기록1건을 추가했다(`Codex / 신규 세션6 / 독립 검토`, `2026-11-09T14:12:57.199776+00:00`). 새 프로세스의 실제 B→A gate가 exit0으로509파일을 검증했다. 이 검토는 사람 신원으로 표시하지 않았다.

## 생성 권한과 전체 준비 CLI

[고정 준비 스크립트](../../../tools/gpu_b_prepare_callback_private.py)는824bytes/SHA `f33a056ec474d24764b9ddca35ffd1d53951a1fc5d7a5f2452f77ca5d5fa3e5d`의 원bytes다. 통제된 umask022 조건에서 실제 UID1000 producer→load_prepared→엄격 consumer가 root/package0700·manifest/settings0600으로 통과했다. CPU 계약1개·9.515초이며 source component31파일을 사용하는 범위다. 과거 원격 디렉터리의 실제 UID/mode는 계속 미확인이다.

별도 새 연결에서는 b85 전체 격리CLI가 보존된 실제 V12 ENV/target/vocab과 fresh prepared 출력을 소비해 REQ08/09/10을 선정하고 ENV·DRAFT EXAMPLE POL·PLAN 및 PACK artifact를 생성했다. guard262파일 origin이 b85와 일치했고 네트워크 수집은 차단했다. 누락 flag의 최초 argparse 오류와, helper 정상 완료 뒤 관측기의 PACK DB행 오가정으로 wrapper가 exit1인 사실을 보존했다. 같은 CLI/producer를 다시 실행하지 않고 원 Store.resolve/get_pack의 read-only 후속 검증으로 결과를 확인했다. process를 exit0으로 소급 표시하지 않았다.

전체 준비 결과는 제품 EVD/RES/RPT0이며 정책 값·max_input8·원 입력과 Ref를 유지했다. 독립 검토432확인의 불일치0, proof SHA `f36edb034b7335bf55898e0c37e8c2c8cc4078bcca68aeef45fdf1167af9bd12`. 실제 신규 source2cb의 node origin은 이후 운영 preflight에서 확인한다.

## 실제 실행에서 추가 확인할 것

원 SOURCES7 계약은 유지하고, 실제 이미지의 `vllm/utils/__init__.py` 한 파일을 같은 현재 Pod 세대 전후에 별도로 읽어 확인한다. 기대 전체1609bytes/SHA는 `c58b3f45deeb98bccca22c945526fd15d483a2f5eb8a3c62cf7df218b081885f`다. 함수 source text SHA `ec67b6dae269ff1d6f0842a0c1e5ecfd31a4e58c2e6f5fd12d76e219c8d8c909`와 AST dump SHA `0ac1a70b4d0b8f03524306e0a91a8d1826ebeeedcadbb682f45343e797232f92`를 다른 값으로 관리한다. 실행 전 검토에서 이 둘의 혼동을 발견해 r4로 정정했고 원 r3 오류도 보존했다.

CPU·준비 완료는 실제 신규 source 설치/Ready·선정 native 요청 대응·EVD/RES/RPT·반출/복원/저장팩 재판정·정리의 성공이 아니다. 기존 b85 V12 증거는 그 source 결과로 보존하며 신규 actual 결과는 별도 기록한다.
