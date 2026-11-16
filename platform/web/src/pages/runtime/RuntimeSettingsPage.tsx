// 시스템 정보와 런타임 설정·인증을 조회한다. 플랫폼 로그인은 별도로 관리한다.
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
      <article id="system-info" className="card card-pad stack">
        <h3>시스템 정보</h3>
        {meta.isError ? <StateView kind="disconnected" detail="자료 출처와 연동 정보를 불러오지 못했습니다. 연결 설정을 확인하세요." /> : meta.data ? <>
          <KeyValue items={[
            ["연동 규격", `contracts ${meta.data.contracts_version}`],
            ["런타임 연결", ({ connected: "정상", example: "예시 자료 사용", disconnected: "연결 끊김", unauthorized: "인증 실패" } as Record<string, string>)[meta.data.runtime_connection] ?? "확인 안 됨"],
          ]} />
          <h4>자료 출처</h4>
          <KeyValue items={meta.data.sources.map((source) => [
            ({ runtime: "런타임", update: "업데이트 검증", evidence: "증거", store: "정책·환경 저장소" } as Record<string, string>)[source.name] ?? source.name,
            ({ live: "서버 연결", file: "저장 파일", example: "예시 자료" } as Record<string, string>)[source.mode] ?? "확인 안 됨",
          ])} />
        </> : <p className="small muted">시스템 정보를 불러오는 중입니다.</p>}
      </article>
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
                    ["런타임 주소", <span className="mono">{runtimeSource?.mode === "live" ? runtimeSource.detail : "예시 자료(연결 안 함)"}</span>],
                    ["실행 환경", s.environment ?? "기록 없음"],
                    ["실행 ID", <span className="mono">{s.run_id ?? "기록 없음"}</span>],
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
                <p className="notice">플랫폼 로그인과 런타임 조회 인증은 별도로 설정합니다. 조회 인증 정보는 플랫폼 서버에서 관리합니다.</p>
              </article>
            </div>
          );
        }}
      </Query>
    </div>
  );
}
