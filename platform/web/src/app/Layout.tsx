import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation, useSearchParams } from "react-router-dom";
import { useIsFetching, useQueryClient } from "@tanstack/react-query";
import { useDocList, useMeta, useRevalidation, useRuntimeStatus } from "../api/hooks";
import { useRuntimeProblems, type RuntimeProblems } from "../pages/runtime/problems";
import { Banner, Dot } from "../components/Misc";
import { SOURCE_MODE, label } from "../lib/labels";
import { PERIODS, fromLocalInput, toLocalInput, type PeriodKey } from "../lib/time";
import { carry, useGlobalFilters, useParams } from "../lib/url";
import { AREAS, areaFor, titleFor, type AreaKey } from "./nav";
import l from "./Layout.module.css";

const THEME_KEY = "byul-platform-theme";
const AREA_KEY = "byul-platform-area";

/** 마지막으로 있던 영역. 두 영역에 걸친 화면(증거 목록)에서 사이드바를 정하는 데만 쓴다. */
function useArea(pathname: string): AreaKey {
  const [last, setLast] = useState<AreaKey | null>(() => {
    try {
      return (sessionStorage.getItem(AREA_KEY) as AreaKey | null) ?? null;
    } catch {
      return null;
    }
  });
  const area = areaFor(pathname, last);
  useEffect(() => {
    if (area === last) return;
    setLast(area);
    try {
      sessionStorage.setItem(AREA_KEY, area);
    } catch {
      /* 저장 못 해도 주소로 영역을 정한다 */
    }
  }, [area, last]);
  return area;
}

function storedTheme(): "light" | "dark" | null {
  try {
    const v = localStorage.getItem(THEME_KEY);
    return v === "light" || v === "dark" ? v : null;
  } catch {
    return null;
  }
}

function effectiveDark(): boolean {
  const t = document.documentElement.dataset.theme;
  if (t === "dark") return true;
  if (t === "light") return false;
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false;
}

/** serving-runtime처럼 OS 설정을 따르고, 누르면 반대 모드를 기억한다. */
export function ThemeToggle() {
  const [dark, setDark] = useState(false);
  useEffect(() => {
    const t = storedTheme();
    if (t) document.documentElement.dataset.theme = t;
    setDark(effectiveDark());
  }, []);
  const toggle = () => {
    const next = effectiveDark() ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem(THEME_KEY, next);
    } catch {
      /* 저장 못 해도 이번 화면에는 적용된다 */
    }
    setDark(next === "dark");
  };
  return (
    <button type="button" className={l.iconButton} onClick={toggle} aria-label="화면 모드 전환" title="화면 모드 전환">
      {dark ? (
        <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
          <circle cx="12" cy="12" r="4" fill="none" stroke="currentColor" strokeWidth="1.8" />
          <path d="M12 2v2.5M12 19.5V22M2 12h2.5M19.5 12H22M4.9 4.9l1.8 1.8M17.3 17.3l1.8 1.8M4.9 19.1l1.8-1.8M17.3 6.7l1.8-1.8" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
        </svg>
      ) : (
        <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
          <path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />
        </svg>
      )}
    </button>
  );
}

function Sidebar() {
  const [params] = useSearchParams();
  const { pathname } = useLocation();
  const areaKey = useArea(pathname);
  const current = AREAS.find((a) => a.key === areaKey)!;
  const meta = useMeta();
  const reval = useRevalidation();
  const keep = carry(params);
  const openReval = reval.data?.items.filter((r) => r.status === "open").length ?? 0;
  const problems = useRuntimeProblems();
  const conn = meta.data?.runtime_connection ?? (meta.isError ? "disconnected" : null);
  const connText: Record<string, string> = { connected: "연결됨", disconnected: "연결 끊김", unauthorized: "인증 거부", example: "예시 데이터" };
  const badgeOf = (key: string | undefined): number => {
    if (!key) return 0;
    if (key === "revalidation") return openReval;
    return problems ? (problems[key as keyof RuntimeProblems] as number) || 0 : 0;
  };
  // 오류 성격(처리 오류·Gateway 비활성·실패 등)은 빨강, 나머지 경고는 강조색(runtime 대시보드와 같음)
  const errorBadges = new Set(["requests", "policy", "cilium", "lab"]);

  return (
    <aside className={l.sidebar}>
      <Link to={`/${keep}`} className={l.brand} title="첫 화면으로">
        <span className={l.brandName}>byul</span>
        <span className={l.brandTag}>PLATFORM</span>
      </Link>
      <div className={l.areaSwitch} role="tablist" aria-label="영역">
        {AREAS.map((a) => (
          <Link
            key={a.key}
            to={`${a.home}${keep}`}
            role="tab"
            aria-selected={a.key === current.key}
            className={`${l.areaTab} ${a.key === current.key ? l.areaTabActive : ""}`}
          >
            {a.short}
          </Link>
        ))}
      </div>
      <nav className={l.nav} aria-label={`${current.title} 화면`}>
        <span className={l.areaTitle}>{current.title}</span>
        {current.groups.map((g) => (
          <div key={g.title} className={l.group}>
            <span className={l.groupTitle}>{g.title}</span>
            {g.items.map((i) => (
              <NavLink key={i.path} to={`${i.path}${keep}`} className={({ isActive }) => `${l.link} ${isActive ? l.active : ""}`}>
                <span className={l.navLabel}>{i.label}</span>
                {badgeOf(i.badge) ? (
                  <span className={`${l.navBadge} ${errorBadges.has(i.badge ?? "") ? l.navBadgeError : ""}`}>{badgeOf(i.badge)}</span>
                ) : null}
              </NavLink>
            ))}
          </div>
        ))}
      </nav>
      <div className={l.footer}>
        <div className={l.footItem}>
          <Dot state={conn === "connected" ? "ok" : conn === "example" ? "warn" : conn ? "bad" : "none"} />
          <span className={l.footText}>
            <span>runtime</span>
            <span className={l.navSub}>{conn ? connText[conn] : "확인 중"}</span>
          </span>
        </div>
        <div className={l.footItem}>
          <Dot state={meta.data?.example_mode ? "warn" : "none"} />
          <span className={l.footText}>
            <span>자료 출처</span>
            <span className={l.navSub}>
              {meta.data
                ? meta.data.sources.map((x) => `${x.name === "store" ? "저장소" : x.name === "evidence" ? "증거" : x.name} ${label(SOURCE_MODE, x.mode)}`).join(" · ")
                : "–"}
            </span>
          </span>
        </div>
        <div className={l.footItem}>
          <Dot state="none" />
          <span className={l.footText}>
            <span>계약</span>
            <span className={l.navSub}>{meta.data ? `contracts ${meta.data.contracts_version}` : "–"}</span>
          </span>
        </div>
      </div>
    </aside>
  );
}

function pad(n: number) {
  return String(n).padStart(2, "0");
}

function Pagebar() {
  const { pathname } = useLocation();
  const { period, from, to, env } = useGlobalFilters();
  const [, patch] = useParams();
  const envs = useDocList("environments");
  const meta = useMeta();
  const qc = useQueryClient();
  const fetching = useIsFetching();
  const [refreshedAt, setRefreshedAt] = useState(() => new Date());

  const refresh = async () => {
    await qc.invalidateQueries();
    setRefreshedAt(new Date());
  };
  const d = refreshedAt;

  return (
    <header className={l.pagebar}>
      <h1 className={l.title}>{titleFor(pathname)}</h1>
      <div className={l.filters} aria-label="표시 범위">
        <label className={`${l.chip} ${period !== "all" ? l.chipActive : ""}`}>
          <span className={l.chipKey}>기간</span>
          <select
            value={period}
            onChange={(e) => patch({ period: e.target.value as PeriodKey, ...(e.target.value !== "custom" ? { from: null, to: null } : {}) })}
          >
            {PERIODS.map((p) => (
              <option key={p.key} value={p.key}>
                {p.label}
              </option>
            ))}
          </select>
        </label>
        {period === "custom" && (
          <>
            <label className={l.chip}>
              <span className={l.chipKey}>시작</span>
              <input type="datetime-local" value={toLocalInput(from)} onChange={(e) => patch({ from: fromLocalInput(e.target.value) })} />
            </label>
            <label className={l.chip}>
              <span className={l.chipKey}>끝</span>
              <input type="datetime-local" value={toLocalInput(to)} onChange={(e) => patch({ to: fromLocalInput(e.target.value) })} />
            </label>
            <button type="button" className={l.chipReset} onClick={() => patch({ period: null, from: null, to: null })} title="기간을 전체로 되돌림">
              기간 초기화 ×
            </button>
          </>
        )}
        <label className={l.chip}>
          <span className={l.chipKey}>환경</span>
          <select value={env ?? ""} onChange={(e) => patch({ env: e.target.value || null })}>
            <option value="">전체</option>
            {envs.data?.items.map((e) => (
              <option key={e.doc_id} value={e.doc_id}>
                {e.doc_id}
                {e.target_ref ? ` (${e.target_ref})` : ""}
              </option>
            ))}
          </select>
        </label>
        {meta.data && (
          <span className={`${l.chip}`} style={{ padding: "0 8px", color: meta.data.example_mode ? "var(--warn)" : "var(--text-2)" }}>
            {meta.data.example_mode ? "예시 데이터" : "실제 연결"}
          </span>
        )}
      </div>
      <div className={l.right}>
        <span className={l.refreshState} role="status" title={refreshedAt.toISOString()}>
          {fetching > 0 ? "불러오는 중…" : `마지막 ${pad(d.getHours())}시 ${pad(d.getMinutes())}분 ${pad(d.getSeconds())}초`}
        </span>
        <button type="button" className={l.iconButton} onClick={refresh} disabled={fetching > 0} aria-label="새로고침" title="새로고침">
          <svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true">
            <path d="M20 12a8 8 0 1 1-2.34-5.66M20 4v4h-4" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
        <ThemeToggle />
      </div>
    </header>
  );
}

function pad2(n: number) {
  return String(n).padStart(2, "0");
}

/** runtime 대시보드처럼 연결이 끊기면 마지막 성공 시각과 함께 띠를 띄우고 화면을 흐리게 한다. */
function useRuntimeStale(): string | null {
  const q = useRuntimeStatus();
  if (!q.isError) return null;
  const last = q.dataUpdatedAt ? new Date(q.dataUpdatedAt) : null;
  const since = last ? `마지막 성공 ${pad2(last.getHours())}시 ${pad2(last.getMinutes())}분 ${pad2(last.getSeconds())}초 기준 데이터` : "아직 받은 데이터 없음";
  return `runtime 연결 끊김 — ${since}`;
}

export function Layout() {
  const meta = useMeta();
  const stale = useRuntimeStale();
  return (
    <div className={l.shell}>
      <Sidebar />
      <div className={l.main}>
        <Pagebar />
        {meta.data?.example_mode && (
          <Banner kind="example">
            예시 데이터 — {meta.data.sources.filter((x) => x.mode === "example").map((x) => x.name).join(", ")} 출처가 예시 자료를 읽고 있습니다. 공식 판정 근거가 될 수
            없습니다.
          </Banner>
        )}
        {stale && <Banner kind="unofficial">{stale}</Banner>}
        <main style={stale ? { opacity: 0.5, transition: "opacity 0.2s" } : undefined}>
          <Outlet />
        </main>
      </div>
    </div>
  );
}
