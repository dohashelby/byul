import { Link, useSearchParams } from "react-router-dom";
import { useRuntimeChecks } from "../../api/hooks";
import { DataTable } from "../../components/DataTable";
import { EventWindowNote } from "./EventWindowNote";
import { Query, StateView } from "../../components/Misc";
import s from "./RuntimeCheckScope.module.css";

export function RuntimeCheckScope() {
  const q = useRuntimeChecks();
  const [params, setParams] = useSearchParams();
  const selected = params.get("check");
  return <Query q={q}>{(data) => <div className="stack">
    <p className="small muted">선정 {data.selected_count}개 · 제외 {data.excluded_count}개 · 팩 판본 {data.pack_revision} · 연결표 {data.mapping_version}</p>
    <EventWindowNote data={data} />
    {selected && <p>점검 {selected} · <button type="button" onClick={() => { const next = new URLSearchParams(params); next.delete("check"); setParams(next); }}>전체 보기</button></p>}
    <DataTable
      columns={[
        { id: "check", header: "점검", cell: (c) => <div className={s.cell}><span className="mono">{c.framework_id}</span><br /><span className="small muted">UPDATE {c.update.id} · 판본 {c.update.revision}</span><p>{c.question}</p></div>, sort: (c) => c.framework_id },
        { id: "condition", header: "적용 조건·관측원", cell: (c) => <div className={s.cell}>{c.condition}<p className="small muted">관측원 {c.sources.join(", ") || "미연결"}<br />규칙 {c.rules.join(", ") || "없음"}<br />개발 {c.devs.join(", ")}</p></div> },
        { id: "limits", header: "지원 범위·남은 확인", cell: (c) => <div className={s.cell}>{c.limitation}<p className="small muted">{c.update.retired ? "UPDATE 은퇴 항목" : c.update.executor ? `UPDATE 실행기 ${c.update.executor}` : "UPDATE 실행기 없음"}</p></div> },
        { id: "observed", header: "현재 조회 사건", cell: (c) => <div>{c.observation_state === "observed" ? <Link to={`/runtime/events?check=${encodeURIComponent(c.update.id)}`}>관련 사건 {c.observed_events}건</Link> : "미관측"}</div>, sort: (c) => c.observed_events },
        { id: "evidence", header: "원증거", cell: (c) => c.evidence_refs.length ? <ul>{c.evidence_refs.map((ref) => <li key={`${ref.evidence_id}/${ref.sha256 ?? ""}`}><Link to={`/evidence?evidence_id=${encodeURIComponent(ref.evidence_id)}${ref.sha256 ? `&sha256=${encodeURIComponent(ref.sha256)}` : ""}`}>{ref.evidence_id}</Link></li>)}</ul> : "연결된 증거 없음" },
      ]}
      rows={data.items.filter((c) => !selected || c.framework_id === selected || c.update.id === selected)}
      rowKey={(c) => c.framework_id}
      initialSort={{ id: "check", dir: "asc" }}
      empty={<StateView kind="empty" detail="해당 점검의 연결 자료가 없습니다." />}
      pageParam="scope_page"
    />
  </div>}</Query>;
}
