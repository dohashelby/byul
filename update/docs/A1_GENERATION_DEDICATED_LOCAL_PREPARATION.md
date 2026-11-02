# OBS02 native20~22 전용 소비의 로컬 준비

현재 우선순위는 PR171 안정화다. 이 소스는 별도 A-1 로컬 작업 공간에서 준비하며, PR171 및 공개 브랜치의 고정 source/gate는 유지한다. 새 기능 PR·원격 기능 push·실제 환경 작업은 하지 않는다.

PR171의 `dd5077c`를 시작점으로 PR172 `69b27f8`의 공통 metadata 두 파일과 기존 OBS02 실행기 변경을 선별 재사용한다. A-2 PR173 `ceff461`의 원본 네 파일을 그대로 소비한다. PR170 다섯 전달 파일과 이미 포함된 B 모듈 이동·등록을 다시 병합하거나 오래된 파일로 덮지 않는다.

OBS02 기존 `observation_identity:kubernetes` 경로에서 profile20/21/22의 공통 `project_file_contexts` 및 `attach_configured_models` 후에 A-2 `native_generation_model_files.analyze(packet, prepared)`와 `attach_configured_models`를 호출한다. 공통과 전용 실패 경계를 구분하고, 후속 attach 오류에는 이미 반환된 분석과 앞선 모델 후보를 유지한다. 부모 collect가 gap을 재계산한 뒤에도 공통/전용의 닫힌 진단을 전달한다. 실제 수집은 기존 public capture 경로이며 새 전송·프로브·판정·팩·core/models/store/judges·외부 contracts는 만들지 않는다.

전용 소비는 17개 고정 공통 source pin과 선택한 session/ENV/target/run/source/image/model revision/inventory/process PID/start/worker 세대의 기존 경계를 유지한다. 원본 native0.32 미지원과 lab/source/image gate를 완화하지 않는다. MEASURED scope 표기는 실제 승인·tensor 소비 신뢰를 부여하지 않는다. profile20의 paired span과 profile21/22의 미제공 span을 구분하며 후자는 availability false와 missing을 유지한다.

검토할 범위는 현재 통합392파일에서 새 공통 모듈과 새 A-2 소비기가 추가된394파일이다. 설치 guard 범위는284→286이다. PR172/173의 과거390/391파일 pin을 현재 통합에 그대로 사용하지 않는다. 검토된 코드 checkpoint를 고정한 뒤에만 A-1 literal을 갱신하고 원 B c9/helper858·승인 세 조합은 보존한다. 이 로컬 조합은 B가 승인한 것으로 처리하지 않는다.

실행자는 기존 OBS02 CLI이며 CPU/EXAMPLE 회귀만 계획했다. 정상·partial·다른 모델·후속 record/첨부 오류, 전용 analyze/attach 전후 오류, 공통 attach 전후 오류를 선택한다. EVD/RES/RPT·verify·반출/복원·저장팩 정확 Ref 재판정을 연결하고 기존 parser/gate를 대체하지 않는다. 실증 없이 실제 승인/검사 생산자·최종 tensor 소비·실제 worker/GPU·전체 OBS02 또는 부모 A39 완료로 집계하지 않는다. 독립 FAIL을 증명할 실제 입력과 정책은 별도이며 모든 예제 결과를 실제 FAIL/PASS 근거로 승격하지 않는다.

필수 CI5와 정식 원격 통합은 PR171 안정화 뒤 별도다. 기존 GPU 승인창 종료·추가 Start0·46.66USD 상한·보존120GiB EBS 이력은 유지한다. 새 정책값·IAM·기간·비용 승인을 추정하지 않는다.

## 고정 검증과 남은 범위

제품 코드 고정은 `d826a7e03acbc05b3bc00861e7e8d7f3ebf1ff74`, 마지막 시험 고정은 `788b8c77d7f875542041ce2968fb3b67307cdf0d`다. 두 소스 사이 변경은 새 회귀 시험의 mapping 필드 assertion뿐이다. 처음 Windows3.12.14/Linux3.12.3에서 각각129통과·6실패를 보존했다. 6건은 정식 mapping 출력에 없는 model_candidates를 참조한 KeyError였으며, 실제 samples·contexts를 모두 확인하도록 보수했다. 이후 보수6사례+인접94사례가 두 OS에서 각각100통과했다. 이를 단일 source의135전체 재실행 성공이라고 쓰지 않는다. Linux3.12.15에서는 새 전용 소비·제품 경계57개가 통과했다.

현재 fullscope literal은 검토 checkpoint `716c29a30dddc3c58bb688d1ad091b414355a909`다. 전체394파일 manifest SHA256은 `0ee4b9da536307f9f5d68b55f3b938c591260da3386dc401d6a01a51a9ebc3c2`, 로컬 gate 파일 SHA256은 `17c9c8f87840dec1dd47efa1780add598e5b6ac69ad4948bc9483e3de7d3cd36`이다. 이 조합을 B 승인 목록에 넣지 않았다. 현재 #171에서 B가 검토할 source는 계속 dd5077c의 gate00979cf6/common df6255e다.

같은 wheel `64dd71617112d9e16100a9ca0ace8876e3c8dc22222228e0c2d80ea550c9330d`의 Windows3.12.14/Linux3.12.3 설치본은 각각guard286파일 전부 일치하며 guard import가 설치 경로 안에 있음을 확인했다. 설치본은 profile20/21/22 정상과21 전용 attach 이후 오류의 기존 CLI4사례·24정확Ref를 확인했다. 소스 시험은 각 Windows3.12.14/Linux3.12.3/Linux3.12.15에서 새30사례·180Ref의 원본 증거 zip을 보존하고 Linux 반출 checksum 일치를 확인했다. 이전 store/restored store와 첫 실패도 남겼다.

테스트의 library identity는 명시적 EXAMPLE 대역이다. 고정 source digest 검사와 parser·정식 CLI 경로를 유지하지만 실제 라이브러리/이미지·serving/worker/GPU·승인·최종 tensor 소비 근거로 집계하지 않는다. 새 join의 profile21/22 paired spans는 여전히 unavailable이며 private journal을 전용 소비기에서 재구성하지 않았다. 정확 통합 HEAD CI5는 아직 실행하지 않았고, 원래 #171의 B15 보수와 소유 검토가 우선이다. 새 기능 PR·원격 기능 push는0이다. 실제 독립 FAIL/정책·생산자 검증과 재기동 실증은 별도 미완료다.
