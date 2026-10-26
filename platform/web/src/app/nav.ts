export type NavItem = { path: string; label: string; badge?: string; match?: string };
export type NavGroup = { title: string; items: NavItem[] };
export type AreaKey = "input" | "update" | "runtime";
export type Area = { key: AreaKey; title: string; short: string; home: string; groups: NavGroup[] };

// 화면은 세 영역으로 나뉜다. 첫 화면(/)에서 영역을 고르면 사이드바에는 그 영역 메뉴만 뜬다.
// 증거 목록은 update·runtime 양쪽이 만든 증거라 두 영역 모두에 둔다.
const EVIDENCE: NavGroup = { title: "증거", items: [{ path: "/evidence", label: "증거 목록" }] };

// Runtime 묶음은 삭제된 runtime 대시보드의 메뉴 구성(요청 통제·관측·통제 적용)을 그대로 따른다.
// badge는 그 대시보드의 경고 수(renderNav) 키다.
export const AREAS: Area[] = [
  {
    key: "input",
    title: "정책 · 환경 입력",
    short: "정책·환경",
    home: "/environments",
    groups: [
      {
        title: "입력",
        items: [
          { path: "/environments", label: "환경" },
          { path: "/policies", label: "정책" },
        ],
      },
    ],
  },
  {
    key: "update",
    title: "Update · 격리 검증",
    short: "Update",
    home: "/update/results",
    groups: [{ title: "검증", items: [{ path: "/update/results", label: "검증 결과" }] }, EVIDENCE],
  },
  {
    key: "runtime",
    title: "Runtime · 운영 통제",
    short: "Runtime",
    home: "/runtime/overview",
    groups: [
      {
        title: "요청 통제",
        items: [
          { path: "/runtime/overview", label: "현황", badge: "overview" },
          { path: "/dashboard", label: "대시보드" },
          { path: "/runtime/requests", label: "요청", badge: "requests" },
          { path: "/runtime/policy", label: "정책·기준", badge: "policy" },
          { path: "/runtime/target", label: "대상·구성", badge: "target" },
          // 재검증 요청은 runtime이 운영 중 변화(자산·구성 drift·정책 내용)를 보고 연다.
          { path: "/runtime/revalidation", label: "재검증 요청", badge: "revalidation" },
        ],
      },
      {
        title: "관측",
        items: [
          { path: "/runtime/observe/collectors", label: "수집원", badge: "observe" },
          { path: "/runtime/observe/hubble", label: "Hubble" },
          { path: "/runtime/observe/tetragon", label: "Tetragon", badge: "tetragonFlagged" },
          { path: "/runtime/events", label: "사건 로그" },
        ],
      },
      {
        title: "통제 적용",
        items: [
          { path: "/runtime/enforce/cilium", label: "Cilium", badge: "cilium" },
          { path: "/runtime/enforce/lab", label: "lab 시험", badge: "lab" },
        ],
      },
      { title: "설정", items: [{ path: "/runtime/settings", label: "설정·인증" }] },
      EVIDENCE,
    ],
  },
];

const hit = (pathname: string, path: string) => pathname === path || pathname.startsWith(`${path}/`);

/** 주소가 속한 영역. 두 영역에 걸친 화면(증거 목록)은 직전에 있던 영역을 따른다. */
export function areaFor(pathname: string, last: AreaKey | null): AreaKey {
  const owners = AREAS.filter((a) => a.groups.some((g) => g.items.some((i) => hit(pathname, i.path))));
  if (owners.length === 1) return owners[0].key;
  if (last && owners.some((a) => a.key === last)) return last;
  return owners[0]?.key ?? last ?? "runtime";
}

export function titleFor(pathname: string): string {
  for (const a of AREAS) for (const g of a.groups) for (const i of g.items) if (hit(pathname, i.path)) return i.label;
  return "byul";
}
