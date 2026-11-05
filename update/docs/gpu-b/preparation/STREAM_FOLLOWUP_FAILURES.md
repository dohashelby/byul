# stream 수집의 후속 보고 오류와 진단 보존

고정 후보는 M09 초기화 진단 수정 b31bf2a를 기준으로 분리했다. 현재 M10 제품3bba·준비acf40에는 적용하지 않는다.

기존 stream collector는 native/RSS 수집이 끝난 뒤 reader 또는 HTTP report()가 예외를 내면 collector 전체가 빠져나갔다. 기존 core의 빈 ABORTED 경계로 들어가 앞서 확보한 직접 RSS 초과 관측과 독립 FAIL이 UNKNOWN으로 바뀌었다. 수집 진단으로 ABORTED가 된 경우에도 이유가 null이었다.

수정 전 직접 회귀3개 실패를 보존했다. 실제 native 자산/observer와 EXAMPLE OS·HTTP 경계를 사용하는 기존 producer→consumer→registry→EVD/RES/RPT→bundle→저장팩 재판정 계약을 재사용했다. 두 report 경계에만 후속 예외를 넣어 기존 RSS FAIL이 없어지는 것을 재현했다. 별도 after-timeout 실제 collect에서 null 사유도 확인했다.

수정은 GPU 담당 소유 kubernetes_stream_buffer.py만 바꾼다. 최종 report의 예외를 고정 진단으로 남기고 불완전 수집/정리 상태를 보존하며 이미 확보한 samples를 버리지 않는다. 기존 gap의 ABORTED 사유가 비었을 때 진단·정리 고정 코드를 전달한다. 기존 사유가 있으면 유지한다. 실패에 포함된 원 예외 문자열은 공개 증거에 남기지 않는다. 관측·정책·판정·기한·센서·원 source gate는 완화하지 않았다.

수정 후 새 직접3개와 영향 회귀39개가 통과했다. 원 로그는 이 worktree의 out/followup/before-fix.log, after-fix.log, regression.log다. 실제 GPU 실행은 0이며 EXAMPLE RSS 근거를 제품 GPU 관측으로 세지 않는다. 기본 팩이나 검토 source manifest를 수정하지 않았고 공식 소스 통합·통합본 필수 전체 시험/설치가 남는다.

이 수정은 M10의 transport 미지원과 별개다. 원 M10은 COMPLETED/EXAMPLE·세 축 UNKNOWN이며 native12 80개 transport_kind=unsupported였다. 공통 sensor는 asyncio의 정확한 _SelectorSocketTransport 타입만 지원한다. 실제 타입/누락 지점은 원자료만으로 확정되지 않으므로 uvloop가 원인이라고 단정하거나 센서 값을 소급 보충하지 않는다. 새 GPU 실행은 공통 관측 수정으로 실제 새 근거가 준비될 때 별도 고정 source/packet으로 요청한다.
