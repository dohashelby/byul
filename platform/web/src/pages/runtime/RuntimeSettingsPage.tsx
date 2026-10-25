// 설정·인증. 삭제된 runtime 대시보드의 "설정"·"사용자" 화면을 옮겼다. 로그인은 없다.
import { useMeta, useRuntimeStatus, RUNTIME_REFRESH_MS } from "../../api/hooks";
import { KeyValue } from "../../components/DetailPanel";
import { PageHead, Query, StateView } from "../../components/Misc";

export function RuntimeSettingsPage() {
  const q = useRuntimeStatus();
  const meta = useMeta();
  const theme = typeof document !== "undefined" ? document.documentElement.dataset.theme : undefined;
  const runtimeSource = meta.data?.sources.find((x) => x.name === "runtime");
  return (
    <div className="page">
      <PageHead title="설정·인증" />
      <Query q={q}>
        {(res) => {
          if (!res.item) return <StateView kind="invalid" />;
          const s = res.item;
          const userState = { not_configured: "인증 미적용", ok: "토큰 인증 적용", error: "인증 설정 오류" }[s.auth.state] ?? "확인 불가";
          return (
            <div className="stack">
              <article className="card card-pad stack">
                <h3>화면</h3>
                <KeyValue
                  items={[
                    ["화면 모드", theme === "dark" ? "다크" : theme === "light" ? "라이트" : "운영체제 설정을 따름"],
                    ["모드 기준", theme ? "직접 선택(이 브라우저에 저장)" : "운영체제 설정을 따름"],
                    ["자동 갱신", `${RUNTIME_REFRESH_MS / 1000}초마다(runtime 화면)`],
                    ["runtime 주소", <span className="mono">{runtimeSource?.mode === "live" ? runtimeSource.detail : "예시 자료(연결 안 함)"}</span>],
                    ["실행 환경", s.environment ?? "미기록"],
                    ["실행 ID", <span className="mono">{s.run_id ?? "미기록"}</span>],
                  ]}
                />
                <p className="notice">이 화면은 읽기 전용입니다. 정책·수집 설정은 Gateway 실행 옵션과 관리 경로의 파일에서 바꿉니다.</p>
              </article>
              <article className="card card-pad stack">
                <h3>인증</h3>
                <KeyValue
                  items={[
                    ["API 인증", <span style={s.auth.state !== "ok" ? { color: "var(--error)" } : undefined}>{userState}</span>],
                    ["방식", s.auth.method ?? "미설정"],
                    ["등록 주체 수", s.auth.state === "ok" ? `${s.auth.principal_count ?? 0}명` : "미설정"],
                    ["보호 경로", s.auth.protected_routes.length ? s.auth.protected_routes.join(", ") : "없음"],
                    ["조회 토큰", "platform 서버가 환경변수 RUNTIME_VIEWER_TOKEN으로 보냄(브라우저에 두지 않음)"],
                  ]}
                />
                <p className="notice">로그인 기능은 없습니다. runtime 조회 API 보호가 켜져 있으면 platform을 RUNTIME_VIEWER_TOKEN과 함께 실행합니다.</p>
              </article>
            </div>
          );
        }}
      </Query>
    </div>
  );
}
