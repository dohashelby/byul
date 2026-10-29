# REQ12 native 출력·collector 관측 계약 /11

공동 기준선 c9af729와 native10 확정 56a752db04c26883206c84bf6ba317f556e2af29를 보존한다. 대상은 A-1 REQ12 SC-18-01/03의 출력 원천·호출 귀속 관측이다. 부모 A39/56/4와 A-1 19/27/2, 늦은 결과·자동 재시도·기업 정책 요구는 그대로 남는다.

`guard prepare-lifecycle-sensor --profile vllm-native-lifecycle-metadata/11 --output <새 외부 경로>`로 명시 선택한다. 기존 `request_termination="kubernetes"`와 REQ12 DRAFT 팩의 select/run/judge/report/verify/evidence export/restore 명령을 소비한다. REQ12 팩 revision3/check7은 기존 77개·다른 76개 정의 bytes와 필수4 POL(정확 ENV/target SHA, aware 유효창)을 보존한다. 새 관측은 `request_attempt_output_events`, `request_attempt_output_links`, `native_output_collector_binding_observed`다. 자동 정책값·PASS/FAIL 조건은 만들지 않는다.

자산은 controller11, 정확 controller10, 정확 private base8, sitecustomize, settings의 5개다. 기본8·Online9·controller10 파일은 변경하지 않는다. 10/8 의존 파일은 고정 SHA와 nofollow·nonblocking FD·전후 파일 신원/크기로 확인한다. 대상 vLLM 공개 커밋 db9527a46873454610df6dbedf79a36d6bf1a7f6의 output_processor.py SHA는 c72d0149e18d6da71d53e23b6550d09310af6917523fef6e282b0907d36b8d54다. 기존 CheckedLoader의 전체 source/실제 실행 code 비교가 통과한 모듈에서만 계측한다. 자기 관측 함수 code 계약은 검토한 자기 소스를 오프라인으로 계산해 CPython3.12.3/3.12.14 optimize0의 리터럴로 고정한다. 실행 대상이 낸 hash를 승인값으로 삼지 않는다.

추가 지점은 `OutputProcessor.add_request`, `RequestState.make_request_output`, `RequestOutputCollector.put/get/get_nowait/close`다. 실제 EngineCoreRequest/RequestState.request_id만 native ID 해시를 제공한다. Collector.request_id와 RequestOutput.request_id는 **외부 ID**이므로 native ID로 바꾸지 않는다. 객체 nonce는 PID 세대·세션 안에서만 의미가 있으며 weakref 최대4096개, 만료/주소 재사용/다른 PID/비weakref 객체/한도 초과는 새 관측 또는 근거 부족으로 처리한다. 주소·객체·출력 강한 참조를 보존하지 않는다.

각 호출의 CALL/RETURN/RAISE를 nonce로 묶는다. 원래 반환 객체·예외/취소 인스턴스·호출 순서와 횟수를 유지한다. put의 입력 출력·저장 출력과 get의 실제 반환 출력을 분리한다. put이 기존 객체를 합치거나 수정했다면 단순 동일 원천으로 연결하지 않는다. 출력은 최대8개·각 텍스트 최대65536 UTF-8 bytes의 SHA/문자수/byte수만 기록한다. 원문·token·logprob·예외문은 저장하지 않는다. pooling·병렬 출력·대형 텍스트·누락/중복/관측 실패는 완전 관측으로 올리지 않는다.

소비자는 같은 recording/PID/start의 유일 selection/native assignment → 성공 add_request와 collector → make 출력 객체 → put 입력/저장 객체·텍스트 SHA → get 반환 객체·텍스트 SHA·실제 caller invocation을 연결한다. 완전한 call 쌍·닫힌 recording·물리 프로세스 결합이 없으면 origin_binding_observed=false다. 외부 ID·prefix·보조 session·시간 순서만으로 객체 원천이나 실제 수신자를 정하지 않는다. 다른 invocation에서 관측한 get은 cross_invocation_candidate이며 confirmed_violation=false다.

collector 반환과 ASGI send 반환은 client 수신·늦은 chunk 전달/폐기·producer 인증·worker/GPU 계산·자동 SDK/Gateway retry의 증명이 아니다. 모든 기존 lab/source/image gate와 native0.32 미지원을 유지한다. 실제 정책/신뢰/이미지/실증 미준비는 UNKNOWN이다. 새 통합 소스의 실제 설치 서버/worker/GPU 실증은 별도이며, CPU EXAMPLE·과거04f7856 두 이미지 실증을 소급하지 않는다.

검증은 실제 공개 메서드 본문 발췌의 collector put/get/close·add_request·make_request_output과 EXAMPLE 의존성으로 수행한다. 원본 전체 파일 SHA·발췌 출처/라이선스는 tests/fixtures/a1-output-native-methods.json에 고정한다. 합치기·동일 예외·중단된 get 취소·weak 만료/주소 재사용/PID·한도·텍스트 한도·metadata/code 변경·누락/중복·옛 프로필 거부·실패 MODULE·제품 CLI의 후속 오류 및 정확6 Ref 왕복을 확인한다. 최종 실행 결과와 파일 bytes SHA는 고정 커밋 인계에 기록한다.

원래 GPU 승인창 종료·추가 Start0·마지막 확인 stopped·기존 총비용을 유지한다. 실제 재개에는 새 시간 승인과 정확 ENV/POL/세대·준비 소비자·소유권/정리/Stop 절차가 필요하다. 원격 CI는 결제 차단으로 runner 실행 여부와 독립 검증을 구분한다.

검증 마감: guard checkpoint ac48622f0926a64fd5f85fd251ed01837c2d623a, 정확 시험 소스 0c12a4d7012dec58763770951cdd73b5c35e93ab. Windows 첫804통과/3실패/6오류/기존skip1·제외1(174.94초)을 보존하고 새 시험만 보수했다. Windows 보수183통과/기존skip1·제외1(42.12/42.776초), 동일1189bytes Linux814통과/제외1(50.06/50.392초),183개 source/wheel/격리설치 파일 일치, 설치본 준비CLI3·제품CLI4의 각정확6Ref 반출/복원·재판정(8.988초)을 확인했다. Linux 실제FD·고정의존성과 owncode 변경거부·소유CPU fork16사례(.044초)도 통과했다. 모든 실행은 CPU/EXAMPLE이며 새 serving/worker/GPU 실증이 아니다. 직전 native10의 Linux전체6942는 그 판본 근거로 보존하고 이번에 변하지 않은 전체시험을 반복하지 않았다.
