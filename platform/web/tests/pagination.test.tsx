import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { useLocation, useSearchParams } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { DataTable } from "../src/components/DataTable";
import { renderAt } from "./helpers";

function List({ count }: { count: number }) {
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const rows = Array.from({ length: params.has("filtered") ? Math.min(count, 30) : count }, (_, index) => ({ id: index + 1 }));
  return <>
    <button type="button" onClick={() => setParams((previous) => {
      const next = new URLSearchParams(previous);
      next.set("filtered", "yes");
      return next;
    })}>필터 적용</button>
    <output data-testid="search">{location.search}</output>
    <DataTable columns={[{ id: "id", header: "번호", cell: (row) => `행 ${row.id}`, sort: (row) => row.id }]} rows={rows} rowKey={(row) => String(row.id)} />
  </>;
}

describe("공통 페이지 번호", () => {
  it("URL 페이지를 복원하고 25행·범위·번호를 표시하며 필터와 정렬에서 첫 페이지로 돌아간다", async () => {
    renderAt("/list?page=3", "/list", <List count={116} />);
    const nav = screen.getByRole("navigation", { name: "페이지 이동" });
    expect(within(nav).getByText("51–75 / 116")).toBeTruthy();
    expect(screen.getByText("행 51")).toBeTruthy();
    expect(screen.queryByText("행 50")).toBeNull();
    fireEvent.click(within(nav).getByRole("button", { name: "5페이지" }));
    expect(within(nav).getByText("101–116 / 116")).toBeTruthy();
    expect(screen.getByTestId("search").textContent).toBe("?page=5");
    fireEvent.click(screen.getByRole("button", { name: "필터 적용" }));
    await waitFor(() => expect(screen.getByTestId("search").textContent).toBe("?filtered=yes"));
    expect(within(nav).getByText("1–25 / 30")).toBeTruthy();
    fireEvent.click(within(nav).getByRole("button", { name: "2페이지" }));
    expect(within(nav).getByText("26–30 / 30")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "번호" }));
    expect(within(nav).getByText("1–25 / 30")).toBeTruthy();
    expect(screen.getByTestId("search").textContent).toBe("?filtered=yes");
  });

  it("페이지가 많으면 중간 번호를 생략하고 짧은 목록에는 조작부를 숨긴다", () => {
    const view = renderAt("/list?page=6", "/list", <List count={300} />);
    const nav = screen.getByRole("navigation", { name: "페이지 이동" });
    expect(within(nav).getAllByText("…")).toHaveLength(2);
    view.unmount();
    renderAt("/list", "/list", <List count={20} />);
    expect(screen.queryByRole("navigation", { name: "페이지 이동" })).toBeNull();
  });
});
