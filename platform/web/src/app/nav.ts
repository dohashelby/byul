export type NavItem = { path: string; label: string; badge?: string; match?: string };
export type NavGroup = { title: string; items: NavItem[] };

// Runtime 묶음은 삭제된 runtime 대시보드의 메뉴 구성(요청 통제·관측·통제 적용)을 그대로 따른다.
// badge는 그 대시보드의 경고 수(renderNav) 키다.
export const NAV: NavGroup[] = [
  { title: "개요", items: [{ path: "/dashboard", label: "대시보드" }] },
  {
    title: "설정",
    items: [
      { path: "/environments", label: "환경" },
      { path: "/policies", label: "정책" },
    ],
  },
  {
    title: "Update (격리 검증)",
    items: [
      { path: "/update/results", label: "검증 결과" },
      { path: "/update/revalidation", label: "재검증 요청", badge: "revalidation" },
    ],
  },
  {
    title: "Runtime · 요청 통제",
    items: [
      { path: "/runtime/overview", label: "현황", badge: "overview" },
      { path: "/runtime/requests", label: "요청", badge: "requests" },
      { path: "/runtime/policy", label: "정책·기준", badge: "policy" },
      { path: "/runtime/target", label: "대상·구성", badge: "target" },
    ],
  },
  {
    title: "Runtime · 관측",
    items: [
      { path: "/runtime/observe/collectors", label: "수집원", badge: "observe" },
      { path: "/runtime/observe/hubble", label: "Hubble" },
      { path: "/runtime/observe/tetragon", label: "Tetragon", badge: "tetragonFlagged" },
      { path: "/runtime/events", label: "사건 로그" },
    ],
  },
  {
    title: "Runtime · 통제 적용",
    items: [
      { path: "/runtime/enforce/cilium", label: "Cilium", badge: "cilium" },
      { path: "/runtime/enforce/lab", label: "lab 시험", badge: "lab" },
    ],
  },
  {
    title: "증거·설정",
    items: [
      { path: "/evidence", label: "증거 목록" },
      { path: "/runtime/settings", label: "설정·인증" },
    ],
  },
];

export function titleFor(pathname: string): string {
  for (const g of NAV) for (const i of g.items) if (pathname === i.path || pathname.startsWith(`${i.path}/`)) return i.label;
  return "byul";
}
