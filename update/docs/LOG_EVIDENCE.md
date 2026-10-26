# OBS-03·OBS-09 로그 수집과 저장 증거 재검증

이 문서는 OBS-03 실행기 0.3.0·OBS-09 0.2.1의 수집 경계와 읽기 전용 재검증 범위를 설명한다. 엔진 시험·정책의 보안 판정과 **저장한 기록을 다시 읽었을 때 일치하는지**는 다른 확인이다. 새 실행기 종류나 기업 collector를 추가한 작업은 아니다.

OBS-03의 `guard-log-analysis-capture/4`는 `guard-log-predicate-projection/1`이다. 요청 본문은
정규 SHA, 응답·로그 메시지·traceback·token 값은 저장용 고정 표식/null로 투영한다. 원래 표식과
상관 ID는 hash만 남긴다. 정책 필드의 구성 요소와 판정용 키 이름은 유지하고 그 밖의 키는
정책 이름과 겹치지 않는 hash 이름으로 바꾸므로 금지·허용 목록 의미를 바꾸지 않는다.
실제 원문은 메모리에서 먼저 분석하며, 저장 투영에 기존 strict parser/분석기를 다시 적용한
결과가 같은 판정 입력을 보존하는지 대조한다. 일치하지 않거나 저장에 실패하면 ABORTED로
PASS를 보류하고 원문 분석의 안전한 관측·독립 FAIL·다른 성공 첨부를 보존한다.
거부한 JSON 행 수는 파이프별로 유지하며, 재생할 때 저장 표식 행 수와도 대조한다.

저장 투영은 원문 JSON의 복사본이 아니다. `PROJECTION_REPLAY_MATCH`와
`replay_basis: stored_predicate_projection`, `raw_source_replayed: false`로 구별한다.
재생은 투영 bytes·문법·판정 입력을 다시 확인하며 원문 복원·실제 로그 출처 인증이나 새 실측을
주장하지 않는다. 원래 bytes SHA, 투영 UTF-8 SHA와 artifact 파일 SHA도 각각 구별한다.
원래 fixture SHA·요청·수집·정책·팩은 유지한다. OBS-09 원문 저장 전체의 보수는 별도 범위다.

과거 OBS-03 0.2.1의 `guard-log-analysis-capture/3`은 `analysis_profile: guard-log-json/2`를
명시한다. 중복 JSON key(중첩·escape 포함)·비유한 수·파싱 불가 사건으로 전체 수집을
확정하지 않는다. 다른 완전한 사건의 독립 위반은 보존한다. 거부한 행은 저장 전에
고정 미확인 표식으로 대체해 비밀값 제거 과정의 JSON 정규화가 누락을 지우지 않게 한다.
원래 source bytes 해시와 실제 저장 text 해시는 계속 구분하며 원문을 새로 보존하지 않는다.

OBS-09는 컨테이너의 실제 image ID로 image metadata를 조회하고 명시한 pinned
RepoDigest 연결을 확인한다. local config ID와 manifest digest가 다른 정상 구성을
허용하며 단순 문자열 일치로 연결을 생략하지 않는다. 전후 신원·소유·남은 deadline을
유지하고 metadata 누락·오류·신원 변경은 UNKNOWN이다. 과거 기록의 두 해시가 같았다는
사실은 그대로 보존한다.

## 수집 범위

`runtime/bounded.py`는 로그를 읽는 Docker CLI 자식 하나를 만들고 stdout·stderr를 각각 읽는다. 두 독립 reader가 각 파이프의 제한된 prefix만 보존하며 무제한 큐를 쓰지 않는다. 상한 초과·deadline·pipe 읽기 실패는 완전 수집을 막는다. 종료 대상은 이 호출에서 만든 자식 프로세스뿐이다. Docker 데몬·컨테이너·다른 프로세스 그룹은 종료하지 않는다. 파이프를 상속한 다른 프로세스로 인해 EOF를 확인할 수 없어도 완전 수집으로 표시하지 않는다.

- OBS-03 Docker 로그: 파이프별 최대 1,000,000 bytes. 전체 실행 예산 기본 180초, 설정 가능한 `max_duration_seconds`는 1~300초다. 파일·inspect 호출에도 남은 예산을 적용한다.
- OBS-09 native INFO: 파이프별 최대 131,072 bytes, 기존 요청 수·event 수·전체 deadline을 유지한다.
- `container_file`: 파일 descriptor에서 최대 1,000,001 bytes를 읽고 최대 1,000,000 bytes만 보존한다. JSON/base64 전달용 로컬 exec 파이프도 별도 2 MiB 상한이 있다.
- `observed_bytes`는 강제 종료 전에 관측한 양이다. 전체 원본 로그의 총 크기나 누락량을 측정했다고 주장하지 않는다.
- 주입된 기존 Runner의 호출 인자는 유지한다. 실제 파이프 경계를 제공하지 않는 Runner에는 `bounded_at_source: false`를 남기며 제품 경계 검증을 대신하지 않는다. 단위 검증의 대역은 실제 엔진 증거가 아니다.

stdout과 stderr를 이어 붙여 하나의 사건이나 전역 시간 순서를 만들지 않는다. 성공한 OBS-03 Docker logs의 stderr는 컨테이너의 별도 로그 스트림으로 각각 파싱할 수 있다. 실패한 CLI stderr는 진단이며 사건으로 쓰거나 원문을 저장하지 않는다. OBS-09의 검토된 native INFO 프로파일은 `ext://sys.stdout`이다. 별도 stderr가 있으면 PASS의 수집 완결성을 보류한다. 성공한 읽기에서 정확한 trial ID에 연결된 완전한 stderr 사건으로 확인된 독립 중복·메타데이터 위반은 유지한다. 이 사건들로 stdout 프로파일의 정상 대조를 대신하지 않는다.

## 파일·엔진 출처

Linux 컨테이너의 파일 reader는 root부터 각 경로 구성 요소를 directory descriptor와 `O_NOFOLLOW`로 연다. 마지막 대상은 nonblocking descriptor로 열고 `fstat`으로 일반 파일인지 확인한다. symlink·특수 파일·조회 중 교체·수정·잘림은 지원하지 않는다. 읽기 전후와 현재 경로 재조회 결과의 device·inode·size·mtime·ctime을 대조한다. 단일 `resolved_path`나 mtime만으로 일관성을 확정하지 않는다. Windows 호스트에서 Linux 컨테이너에 넣는 Python 코드이며 Windows 파일 API로 대신 검증한 것으로 표현하지 않는다.

컨테이너 ID·이미지 ID·StartedAt를 전후 대조한다. OBS-03의 엔진 source는 실제 target container와 같아야 한다. collector 사본만으로 엔진 로그를 대체하지 않는다. OBS-09는 기존 이미지·owned label·argv·logging 환경의 정확한 프로파일도 유지한다. 이 값들은 호스트나 collector를 암호학적으로 인증하지 않는다.

OBS-03은 JSON leaf의 `request_id` 또는 중첩된 실제 `.request_id` 값이 **정확히 일치하는 사건**만 시험에 연결한다. 임의 필드·유사 ID·과거 traceback 안의 부분 문자열은 연결 근거가 아니다. Docker timestamp가 있으면 실제 캡처 창 안인지 대조한다. 파일의 시간 창은 이번에 만든 고유 ID의 사건 연결을 사용하며 mtime을 요청 시간 증명으로 쓰지 않는다. 정상·error·실제 exception/traceback/dump 내용이 따로 필요하고 단순 4xx는 dump가 아니다.

전후 출처가 같은 완전한 사건의 위반은 이후 timeout·잘림·다른 source 누락으로 지우지 않는다. 출처가 바뀌었거나 완전한 사건인지 확인하지 못한 prefix는 진단 자료이며 엔진 위반으로 확정하지 않는다. JSON이 아닌 로그나 다른 request-ID 필드 프로파일을 임의로 지원했다고 하지 않는다.

## bytes·해시·비밀 처리

`guard-pipe-capture/1`의 retained SHA256는 디코딩·마스킹 전 **실제로 보존한 CLI 파이프 bytes**의 해시다. 컨테이너 파일 reader의 raw 해시는 읽은 파일 prefix bytes다. 둘 다 Docker daemon 원본 파일 전체의 해시나 collector 서명이 아니다. 잘린 UTF-8은 replacement로 구분하고 PASS를 보류한다. 원시 bytes를 먼저 해시하므로 LF/CRLF를 정규화한 값을 원본 해시로 부르지 않는다.

과거 OBS-03 0.2.x와 OBS-09의 artifact는 설정한 인증값과 표지된 자격값을 마스킹한다. 저장된 `persisted_decoded_*`는 마스킹된 text의 정확한 UTF-8 bytes에 대한 별도 해시다. OBS-03 0.3.0의 `*_projection_*`는 원문 값이 없는 저장 투영의 bytes이고 `*_original_*`는 투영 전 메모리 text의 hash/길이다. artifact 자체의 SHA256는 저장 파일 bytes를 검증한다. 마스킹·투영으로 제외한 원문을 재구성하지 않으며 새 검사 결과를 원래의 보안 판정으로 덮어쓰지 않는다. 모든 비밀을 자동으로 식별하는 기능은 아니다. 오류 사유에는 임의 stderr·예외 메시지·설정 본문을 복사하지 않는다.

원시 파이프 상한과 영구 표현의 읽기 예산은 구분한다. 여러 invalid UTF-8 bytes가 replacement로 바뀌거나 짧은 비밀값을 긴 마스킹 표식으로 바꾸면 저장 text가 커질 수 있다. 재검증은 named artifact 전체의 16 MiB 읽기 예산과 정확한 저장 해시를 적용하며 원시 cap을 decoded 길이에 소급 적용하지 않는다. replacement·잘림으로 인한 UNKNOWN은 그대로다. 누락 파일·읽을 수 없는 SQLite 등 저장 입력 I/O 오류는 비밀 없는 고정 검증 오류로 표시하며 원본을 복구하거나 수정하지 않는다.

## 저장 증거 읽기 전용 재검증

설치된 패키지에서 실행한다. source store를 닫고 checkpoint한 다음 새 출력 폴더를 지정한다.

```powershell
python -m guard.log_evidence --store C:\guard-data\original-store --report RPT-...@1 --output C:\guard-data\log-audit-new
```

`tools/verify_log_evidence.py`도 같은 진입점의 얇은 wrapper다. `--report` 생략은 저장 RPT가 정확히 하나일 때만 허용한다. 여러 report나 같은 ID의 여러 revision이 있으면 정확한 `id@revision`을 요구하며 최신 기록을 임의 선택하지 않는다.

검사 대상:

1. immutable/read-only SQLite, 닫히지 않은 WAL/journal 거부, 원본 inventory 전후 대조.
2. RPT manifest의 정확한 PLAN·POL·ENV·EVD·RES Ref와 객체 해시·관계·side.
3. PLAN의 저장 팩과 artifact path·bytes·SHA256. 디스크의 최신 팩을 적용하지 않는다.
4. official·정책/팩 검토 상태·data origin·검증 수준의 원본 입력과의 일치.
5. 저장된 캡처·실행기 버전의 분석 프로파일로 순수 재구성하고 관측과 대조. OBS-03 `/2`+0.2.0은 당시 JSON parser, `/3`+0.2.1은 명시 strict parser, `/4`+0.3.0은 strict parser로 저장 투영을 재생하며 미등록 조합은 확정하지 않는다. 과거 형식·저장 증거에 새 계약을 소급하지 않는다.
6. 저장된 동일 버전 rule_table·원래 params·PASS 관문·CPU 성능 제한으로 판정 축 대조.
7. OBS-09의 캡처→구조화 event→별도 HTML의 정확한 대응.

출력 `summary.json`의 `accepted: true`는 **저장된 로그 재검증이 일치**한다는 뜻이다. 보안 PASS·실제 엔진 신규 실행·기업 collector 검증·공식 승인이라는 뜻이 아니다. 원래 UNKNOWN이나 독립 FAIL의 재구성이 일치할 수도 있으며 `coverage_complete`와 원래 `verdicts`를 함께 읽어야 한다. `evidence_contract_valid`와 한계도 별도 표시한다. `summary.txt`는 같은 범위의 한국어 요약이다.

종료 코드: 0=재검증 일치, 1=미확인·의미 불일치·대상 로그 없음, 2=입력/무결성/출력 경로 오류. 기존 출력은 덮어쓰지 않고 source 안이나 source를 포함하는 상위 출력 경로·symlink/junction을 거부한다.

원래 EVD·RES·RPT는 수정하지 않으며 새 MEASURED 객체도 만들지 않는다. 재판정 결과를 저장하려면 원본을 bundle로 반출한 후 **새 별도 위치**에 복원하고 기존 `guard judge`/`guard report`를 저장 팩으로 실행한다.

## 과거 자료와 미검증 범위

기존 캡처의 정확한 artifact bytes·Ref는 계속 검증하지만 `/2` 이전에 없던 파이프 상한·nofollow·정확한 사건 연결을 소급 인증하지 않는다. `LEGACY_UNVERIFIABLE`로 원래 결과를 보존한다. 과거와 다른 판정 버전밖에 없으면 대체 판정기를 적용하지 않고 `JUDGE_VERSION_UNAVAILABLE`로 표시한다. 마스킹으로 재구성 불가능하거나 저장 관측과 다르면 진단은 남기고 원래 판정을 덮어쓰지 않는다.

OBS-03 `/2`의 replay 일치는 새 strict JSON 거부 기준을 만족했다는 뜻이 아니다.
`analysis_profile`·`strict_json_verified`와 한계에 그 구분을 표시하고 원본 EVD/RES/RPT를 바꾸지 않는다.

이번 검증은 순수 로직, 대역 수집, 로컬 자식 프로세스의 파이프와 Linux 파일 계약, 설치 wheel 재현을 구분한다. 실제 엔진·두 이미지 실행·기업 정책값·로그 구성의 실환경 검증 결과는 별도 기록이 없는 한 미수행이다. 실제 두 이미지 CPU 실증이나 PR #20의 다섯 점검 수락 기준을 대체하거나 완화하지 않는다.

테스트 실행 후 수치·명령·환경은 이번 묶음의 검증 기록과 PROGRESS.md를 따른다.
