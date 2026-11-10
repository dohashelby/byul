# SUP-16 R4 역할 진입 전 실패와 정리

## 실제 실행 결과

기존 선택 GPU1대/TP1에서 2026-11-10 20:54:28 KST 기동 명령 종료, 20:55:09 running/SSM Online, 20:55:30 실행 착수를 확인했다. 사용자는 자원·예산 확인을 직접 맡고 같은 선택 대상을 즉시 재개하도록 승인했다. 새 인스턴스·TP2·증설은 없었다.

42파일 배치와 host health는 성공했으나 whole 명령은 Failed/exit1이다. 실제 wrapper는 정상 원본 준비 후 challenge 모델 복사 단계 `014-fixed-challenge-current-copy`에서 실패했다. 정상-before·challenge-partial·normal-recovered 역할 시험에 도달하지 못했으므로 **실측 성공으로 집계하지 않는다**.

## 원인과 정확 수정 범위

복사기 `copy_fixed_challenge.py`의 `DESTINATION`은 R3-ASSETS/challenge였지만 실제 R4 wrapper가 전달한 destination은 R4-ASSETS/challenge였다. 기존 고정 경로 guard가 line67에서 `COPY_EXACT_SELECTED_PATHS_REQUIRED`로 거부했다. 새 프레임 생성기가 R3 복사기를 그대로 복사하고 manifest의 challenge code Base64/SHA도 그대로 재사용한 연결 누락이다. 원 모델 SOURCE·부분 파일 prefix SHA나 GPU 하드웨어 실패로 단정하지 않는다.

별도로 driver는 probe phase `whole-result-probe`를 발행하는데 결과 선택기는 `whole-result-probe-compressed-v2`를 요구해 `BINDING_PHASE`를 발생시켰다. 원 복사 실패와 후속 증거 선택 실패를 분리한다. 원 phase·flags·자식 목록·FAIL을 성공으로 바꾸지 않는다.

원 R4 묶음은 불변으로 보존한다. 다음 고정본은 새 프레임의 정확 ASSETS/challenge 경로를 복사기 상수·실제 argv·embedded code bytes·manifest SHA/Base64에 함께 연결하고, driver→selector phase도 일치시킨다. 기존 SOURCE·원 reference·prefix SHA·owner/FD·경로 guard·시간 제한은 유지한다. 같은 실패 입력으로 재기동하지 않는다.

## 회수·종료 증거

새 첫 실패 캡슐은 실제 사용됐다. 캡슐14312bytes/SHA prefix `696163`에서 원 stderr297bytes/SHA prefix `09df`와 실제 실패 자식 PID4487/rc1·reaped/group_terminal·정리 오류0을 회수했다. 전체 대형 archive 반출 성공이나 제품 capture 성공은 아니다.

독립 검토에서 현재 API190개와 mutation78개(start1/send77), 수락77CID의 실제 terminal(76Success0/wholeFailed1)을 원 request·stdout/stderr SHA로 연결했다. wrapper4349는 caller_constructed=true/phase_or_outer_blocked=false, 알려진 자식20개가 모두 group_terminal(19rc0/1rc1), pending handoff는 null이다. 실행 부모326도 exit1/reaped/group_terminal이다. 원 R3의 별도 수락 UNKNOWN2건을 이 R4 종료 증거로 해소하지 않는다.

S6 필수 검토 SHA `e8f8556d846fd44129f8d94033ac52c7c4d08861a58962a53e9428f9052e7aa7`, 최종 채널 경계 후속 SHA `dd2ff8d51dedab1b6744fc6e0b1c83d53db8425c0d08347bd74e9fa3fc3704cf`다. 첫 검토의 채널 문자수/UTF8 bytes와 outer stderr/child stderr 구분 가정은 후속에서 바로잡았다. 원자료·원판정 수정은 없다. 사전 검토에서 놓친 모듈 간 경로·phase 연결도 후속에 기록했다.

원 4receipt로 오프라인 회귀를 수행했다. 기존 selector의 BINDING_PHASE를 재현하고, 기대 phase literal1만 수정한 후보에서 `None`(현재 terminal 시도에 archive receipt 없음)을 확인했다. wrong phase/request SHA/CID/nonterminal API/failed probe는 그대로 거부해 총7경계/실패0이다. 후보 SHA `f821eb7b6d17d0bb61c85d06706f971d7b3b6654c5a25fdf790028e60223bbe2`다. S6의 후속 독립 검토14대조/불일치0, SHA `ea607bb9225f0d038dd995e4cd90ab58721019af9d9e553716258be40e46d1d8`로 정확 literal1·guard 불변·원4receipt 연결을 확인했다. Windows 순수 Python receipt 재생이며 native CPU 자격·GPU 실측으로 표시하지 않는다. 원 request/argv/source 독립 대조는 앞의 S6 검토를 재사용했고 전체 archive CRC는 반복하지 않았다.

## 정리 상태

원 driver의 정지 앵커는 21:11:27.414192 KST이며 사전 조회 전에 기록한 값이다. 실제 일반 stop CLI의 running→stopping 수락 결과 시각은 21:11:35.017359 KST다. 앵커와 실제 수락 시각을 구분하며 앞선 인계 표현은 이 원 결과로 정밀화한다. 원 180초 관측은 `STOPPED_NOT_YET_OBSERVED`/exit1로 종료했고 해당 부모·프로세스 그룹 회수는 확인됐다. 21:15:23 후속 조회도 stopping이었다. 원 감시 실패를 그대로 보존한다.

이후 같은 대상의 읽기 전용 조회에서 EC2 21:19:11.789349~13.026860 KST 실제 stopped·공인IP 없음, 이어 SSM node/Pending/Executing0을 확인했고 마지막 조회 종료는 21:19:15.741679 KST다. 이는 각기 다른 조회 시각이며 정확한 off 전이 시각은 미확정이다. Force·재정지·타이머 reset은 0이다. 전체 자원·다른 인스턴스·새 비용 조회를 하지 않았고 새 비용 추정값도 발급하지 않았다. 원2시간 보호를 유지했으며 특정 타이머 실행은 추측하지 않는다.

새 delta207449bytes/263member의 최초 무결성 검사 오류0과 새 CLI62개의 원 request/stdout/stderr 연결을 독립 검토했다. S6 필수 원 검토 SHA `e1bcc9ca935116b3f0f950b3267b561cfdbd915168e8c8e6576b03f2684beec5`의 유효92대조를 재사용하고, 검증기 shape 가정3건은 별도 후속5대조/불일치0(SHA `ad5839a06751b16c738a09d84a2c4a0af1477591c4e269a6a01a83c33c6a6c14`)으로 해소했다. 원95/불일치3 기록은 이력으로 보존했으며 원자료·제품 결함으로 바꾸지 않는다. 현재 R4 정리 필수 검증은 완료됐다. 기존 control archive 검증을 재사용해 전체 재검사를 반복하지 않았다.

PR head `ba1aa295b27e6a621f242e141482ac5dabb77b58`의 CI18개는 모두 성공했다. 이는 해당 코드 head의 소프트웨어 검사 결과다. 실제 세 역할·capture/writer/factory·정확 제품 EVD/RES/RPT·전체 archive 무결성은 별도 잔여이며 기존 LAB10/33·43행의 원 Ref/판정을 바꾸지 않았다. [B03·B04 준비](B03_B04_INPUT_PREPARATION_20261110.md)는 병행한다.
