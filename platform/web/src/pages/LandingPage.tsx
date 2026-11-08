// 첫 화면. 사이드바 없이 세 영역 카드만 둔다: 정책·환경 입력 → Update(격리 검증) → Runtime(운영 통제).
// 카드를 누르면 그 영역으로 들어가고, 사이드바에는 그 영역 메뉴만 뜬다.
import { Link, useSearchParams } from "react-router-dom";
import { useMeta } from "../api/hooks";
import { ThemeToggle } from "../app/Layout";
import { AREAS } from "../app/nav";
import { SourceExampleBanner } from "../components/SourceExampleBanner";
import { carry } from "../lib/url";
import s from "./LandingPage.module.css";

export function LandingPage() {
  const meta = useMeta();
  const [params] = useSearchParams();
  const keep = carry(params);
  return (
    <div className={s.shell}>
      <header className={s.top}>
        <span className={s.brand}>
          <span className={s.brandName}>byul</span>
          <span className={s.brandTag}>PLATFORM</span>
        </span>
        <ThemeToggle />
      </header>
      <SourceExampleBanner meta={meta.data} />
      <main className={s.main}>
        <nav className={s.grid} aria-label="영역">
          {AREAS.map((a) => (
            <Link key={a.key} to={`${a.home}${keep}`} className={s.card}>
              {a.title}
            </Link>
          ))}
        </nav>
      </main>
    </div>
  );
}
