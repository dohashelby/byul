export type NavItem = { path: string; label: string };
export type NavGroup = { title: string; items: NavItem[] };

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
      { path: "/update/revalidation", label: "재검증 요청" },
    ],
  },
  {
    title: "Runtime (탐지·차단)",
    items: [
      { path: "/runtime/requests", label: "요청 내역" },
      { path: "/runtime/events", label: "사건 로그" },
      { path: "/runtime/status", label: "상태" },
    ],
  },
  { title: "증거", items: [{ path: "/evidence", label: "증거 목록" }] },
];

export function titleFor(pathname: string): string {
  for (const g of NAV) for (const i of g.items) if (pathname === i.path || pathname.startsWith(`${i.path}/`)) return i.label;
  return "byul";
}
