# A2 native17 선언 파일 범위·read 후보

SUP03/05/06의 후속 파일 소비 분석을 위해 고정 공통 c0d683b의 native17 입력을 소비한다. 공개 함수는 `guard.collectors.native_model_file_reads.analyze(packet, prepared)`, 판본은 0.1.0, schema는 `guard-native-scoped-file-read-candidates/1`이다. 입력은 A1의 prepared/ENV/target/source/image/lab/code/runtime/session/clock/hashchain 입장 검증을 거친 기존 capture_session packet과 검증된 prepared manifest다. 임의 JSON을 신뢰 관측으로 가져오지 않는다.

A1의 공개 file-read project와 명시적 native17 project_iterations를 재사용한다. 같은 recording·PID/start·root·loader call·model object의 source-verified READ_RETURN과 iterator step만 후보로 결합한다. yield·decoder 반환·root 정상 종료는 선행조건이 아니며, 이미 읽힌 사실은 후속 decoder/callback 실패나 close/recording 누락 뒤에도 별도 상태로 보존한다. unbound·세대 불일치·step 누락은 미결합 locator로 남기고 공통 원관측을 지우지 않는다.

선언된 참조 inventory와 관측 path hash를 각 root/model 그룹에서 대조한다. 같은 이름의 여러 handle/실행을 실제 한 파일 소비로 합치지 않는다. 파일별 returned bytes/hash, FD 안정성, 경로/FD 일치, 전체 FD read, 선언 참조 bytes 일치·불일치, close·recording·root 완료는 각각 유지한다. Windows의 경로 ctime 생성 기준과 FD 변경 기준 차이를 무시해 일치시키지 않는다. hash 비활성·상한 초과·불안정 FD를 참조 불일치로 확정하지 않는다. 누락 파일 개수는 선언 범위의 후보 공백이며 실제 model/rank/shard 전체 coverage가 아니다.

같은 model object에서 여러 loader root가 관측되면 수와 모호성 표시를 함께 남긴다. 각 root·handle·loader call을 보존하며 최신 root나 실제 사용된 파일 세대를 임의로 선택하지 않는다. 출력 한도로 일부 후보를 생략해도 이 관측 수와 한도 공백을 유지하고 전체 coverage를 주장하지 않는다.

security는 UNKNOWN이고 worker assignment·approved model·producer·tensor/backend·decoder completion·nofollow·reference authority·per-file consumption·실제 model generation·전체 inventory/scope truth는 false다. 선언 hash와 파일 bytes 일치가 기업 인가나 실제 serving worker·최종 tensor 적용의 증거가 되지 않는다. normal C safe_open/mmap·torch.load FD·실제 worker/rank/shard/tensor 범위는 미지원이다. 모델 파일을 추가로 open/read하거나 공통 hook·selector·parser·전송·CLI·registry·공유 팩을 복제하지 않는다.

A1 정식 연결은 기존 OBS02 native17 admitted packet 소비 위치에서 별도 `scoped_file_read_candidates` projection을 붙이고, 같은 recording/model·후속 forward 이전 locator만 EVD에 연결하는 최소 변경이다. 기존 공통 eager_file_reads와 weight_iteration의 사실·missing은 보존한다. 후속 전용 소비/수집/보고/첨부 실패는 앞선 유효 사실이나 독립 FAIL을 지우지 않는다. A2는 새 전용 source pin·직접/인접/설치 검증과 원본 bytes를 전달하고, A1의 검토 pin·정식 CLI/Ref 수용과 필요한 실제 환경 확인을 분리한다. 이 helper를 SUP05/06 정식 보안 판정 또는 전체 분기 완료로 처리하지 않는다.
