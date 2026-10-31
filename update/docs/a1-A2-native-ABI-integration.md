# A2 native ABI 확정본 682538f 통합

부모 A39/56/필수4와 A1 19/27/필수2, 공동 시작점 c9af729 및 원본·sources/·팩·Ref·worktree 보존 조건을 유지한다. 이 묶음은 A2 확정 `682538fe532a0d824144cd2f959016198e1f5d9f` / PR170의 원본 5파일을 통합한다. 전용 알고리즘을 재작성하거나 A1이 A2 승인 pin을 대신 바꾸지 않는다.

## 확정 전달물과 소비 경계

- `native_weight_iteration.py`, `native_model_file_reads.py`, `native_model_mappings.py`, `native_worker_model_files.py`와 원 문서 `NATIVE_CONSUMER_ABI_COMPATIBILITY.md`를 commit blob의 원 bytes로 소비했다. 파일별 SHA·크기는 A2 등록 인계와 일치한다.
- 네 소비기의 AST는 부모와 비교해 `SOURCE_PINS` 11개 literal 변경 이외의 분석 실행 코드·공개 함수·schema·판본이 동일하다. A2 소유 파일은 그대로 보존한다.
- 현재 공통 기준은 PR172 `69b27f84015d7384bbcef4df27cd7d43d1f5eb4a`다. native16/17/18 세 소비기의 모든 고정 target bytes는 A2가 검토한 값과 일치한다.
- native19 소비기의 `vllm_worker_startup_metadata.py` 한 pin은 A2 검토 `d122b34e03da52f78f425bca84660b302cac062a889dfecccf8c5393091c25cc`와 현재 `b5d0be2de94fe9453a5f68b58e019b62a2f454bd59718fb43512a321755e651a`가 다르다. 기존 native19 기본 알고리즘의 정적 동등 근거가 있어도 승인값을 자동 갱신하지 않는다. 원 A2 gate의 거부와 공통 관측 보존을 검증하고, A2가 새 정확 target을 검토한 확정본을 제공하면 재개한다.
- A1의 전체 공통 검토 pin은 같은 기존 5개 경로·390파일 범위에 적용한다. B 원 c9 baseline·helper bytes·원 승인 pair는 보존한다. 새 A1 검토값을 B 승인을 대신하는 값으로 해석하지 않는다.

## 명시적인 stale fixture

PR169의 기존 native16 음성 제품 사례는 당시 승인되지 않은 원 소비기를 검증했다. 새 승인 전달물은 생산 소비 경로를 복구하므로, 그 음성 사례는 이제 **명시된 과거 원 소비기 fixture**를 사용한다. `3edb6ca39f99d17d18a6ad699d197adb40bb8635`의 원 blob SHA `fd782cc94436b580c39eaad6a5b40553cd29850bb67710f9a5f1d32cf0c4d4e4`를 확인해 격리 임시 경로에 로드하고, 현재 두 target bytes의 복사본을 대상으로 원 gate가 거부하는지 검사한다. 실제 새 소비기의 pin·코드·배포 경로는 바꾸지 않으며 기존 음성 assert·정확 Ref 검증을 유지한다.

native19 음성 사례는 현재 생산 소비기의 원 pin을 실제로 사용한다. 정상 packet을 원 공개 `analyze`에 전달해 source mismatch를 확인하고, 기존 OBS02 CLI에서 앞선 weight·mapping·startup 사실과 UNKNOWN을 보존한다. 후속 수집/첨부 오류도 정제 진단·ABORTED 및 저장팩 정확 Ref 왕복과 연결한다.

## 검증 범위

초안·상수/원 bytes 검토를 마친 후 소스를 고정한다. 직접 범위는 세 일치 소비기의 원 전용 시험과 native16/17/18 제품의 정상·부분/누락·후속 수집·첨부 사례다. 현재 불일치하는 native19는 새 음성 사례를 검증하며 원 A2 전체 positive 통과를 주장하지 않는다. 인접 범위는 공통 native19·새 generation 파일 투영·원 stale fixture·A1 전체 source gate·불변식이다. 설치본·원팩 반출/복원·저장팩 정확 Ref와 정확 HEAD CI5는 따로 기록한다.

새 native20~22 전용 소비는 이 전달물에 없다. A2가 요청한 공개 startup `spans`의 paired return/callback 사실은 이미 계산한 공통 journal에서 제공할 후속 준비 작업이다. 그 전용 분석을 A1이 대신 만들거나 세 나머지 복구 경로의 선행조건으로 삼지 않는다.

CPU/EXAMPLE·설치·소스 일치는 실제 serving/worker/backend/GPU·승인·최종 tensor·전체 분기 완료가 아니다. 원 GPU 승인창 종료·추가 Start0·46.66USD·과거 사용량·120GiB EBS와 소유권을 유지하며 새 AWS·IAM·기간·비용 확대는 하지 않는다. 실제 신뢰·정책 생산자가 없는 OBS02 판정은 UNKNOWN이다.


## 검증 마감

고정 검증 source `d20f1619912648234199c0736c7dc034a19c681e`, A1 검토 공통 source `9bd56adf55afb40371973ddac57067560bef2990`를 사용했다. 공통390파일 manifest는 `77ef5d0c99550dddbcc26b4ce6a3c0519be72755d3152622350e8bb216c70285`다. 원5경로 검사·원 B helper·c9·B 승인 pair와 모든 A2 원 bytes를 보존했으며 런타임 해시 승인·검사 제외는 없다.

- Windows3.12.14/Linux3.12.15 직접102통과씩. 세 일치 소비기 원 시험·native16/17/18 CLI 각4사례와 stale19 CLI3사례, 합계15사례·정확90Ref씩의 반출·복원·저장팩 재판정을 포함한다.
- 인접/불변식 Windows213통과·기존 INV03 skip1, Linux214통과. 명시 과거 원 소비기 fixture에서 stale16 음성4사례의 원 assert·Ref 검증을 유지했다.
- wheel SHA256 `3780062c80c02bc3a9925240b957cec22d5b42adc842f8910acc2bbbcbc57154`, source d20f161: 두 OS 설치282파일 일치·native16/17/18 정상과 stale19 정상의 CLI4사례 산출물·정확24Ref를 확인했다. 첫 설치 보조도구는 줄인 pytest 임시 폴더명을 긴 prefix로 비교해 exit1이었다. 원 실패를 보존하고 제품을 재실행하지 않았으며, 설치본에서 원 저장소/복원/UNKNOWN·후보/거부 의미와 정확 Ref·원 zip을 별도 읽기 전용 finalizer로 재검증해 마감했다. 이를 최초 wrapper exit0으로 바꾸지 않는다.
- 원 source zip Windows 7e7d20204e36a93ed6976d31e8410a167663b2a326dfbe158d2413d49b1e66a1 / Linux 5ad277494bafc25b4646564eef20415021c205015f3cb6daad43fb92b5a8263e; 원 설치 zip Windows a888ca42bea0a7139aa3cf26d5e77189eabe98071ff489c78d180a0143dbdf0f / Linux 9eeca7359ee4dfe30172c3dbe5a5b36096ee20bed54df8b7c83703b661eb67f8. Linux 반출 bytes는 일치하며 원 exports를 보존했다.

native19의 원 소비기 positive 범위는 아직 승인되지 않았다. 정확 startup metadata pin의 A2 새 확정본이 필요하다. 공통 native20~22 공개 투영은 PR172를 재사용하며, 계산된 startup spans의 공개 전달은 후속 독립 준비 작업으로 확인했다. 전체 suite·정확 HEAD CI5·실제 serving/worker/backend/GPU·승인·tensor 및 A39/A1전체 완료를 주장하지 않는다.
