import { useState, type FormEvent } from "react";
import { changePassword, login, ROLE_NAMES, type AuthUser } from "../api/auth";
import s from "./LoginPage.module.css";

/** 로그인 화면. 회원가입·비밀번호 찾기 없음(관리자가 계정을 발급). */
export function LoginPage({ message }: { message?: string }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setPending(true);
    setError(await login(username.trim(), password));
    setPending(false);
  }

  return (
    <div className={s.wrap}>
      <form className={`card ${s.box}`} onSubmit={submit} aria-label="로그인">
        <div className={s.brand}>
          <span className={s.brandName}>byul</span>
          <span className={s.brandTag}>PLATFORM</span>
        </div>
        {message && !error && <p className={s.note} role="status">{message}</p>}
        <label className={s.field}>
          아이디
          <input className="input" autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} autoFocus />
        </label>
        <label className={s.field}>
          비밀번호
          <input className="input" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>
        {error && <p className={s.error} role="alert">{error}</p>}
        <button className="btn btn-primary" type="submit" disabled={pending || !username.trim() || !password}>
          {pending ? "확인 중…" : "로그인"}
        </button>
        <p className={s.hint}>계정은 관리자가 발급합니다.</p>
      </form>
    </div>
  );
}

/** 비밀번호 변경. 메뉴에서 들어오고, 변경 강제 설정이 켜진 계정은 이 화면만 보인다. */
export function PasswordForm({ user, forced = false, onDone }: { user: AuthUser; forced?: boolean; onDone?: () => void }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);
  const [pending, setPending] = useState(false);
  const mismatch = again.length > 0 && next !== again;

  async function submit(event: FormEvent) {
    event.preventDefault();
    setPending(true);
    const error = await changePassword(current, next);
    setPending(false);
    setResult(error ? { ok: false, text: error } : { ok: true, text: "비밀번호를 바꿨습니다." });
    if (!error) {
      setCurrent("");
      setNext("");
      setAgain("");
      onDone?.();
    }
  }

  return (
    <form className={`card ${s.box}`} onSubmit={submit} aria-label="비밀번호 변경">
      <h2 className={s.title}>비밀번호 변경</h2>
      <p className={s.hint}>
        {user.id} · {ROLE_NAMES[user.role]}
        {forced ? " · 계속하려면 비밀번호를 먼저 바꿔야 합니다" : ""}
      </p>
      <label className={s.field}>
        현재 비밀번호
        <input className="input" type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} />
      </label>
      <label className={s.field}>
        새 비밀번호
        <input className="input" type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} />
      </label>
      <label className={s.field}>
        새 비밀번호 확인
        <input className="input" type="password" autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} />
      </label>
      <p className={s.hint}>10자 이상, 아이디·admin과 다르게, 서로 다른 문자 4개 이상.</p>
      {mismatch && <p className={s.error}>새 비밀번호가 서로 다릅니다.</p>}
      {result && <p className={result.ok ? s.note : s.error} role={result.ok ? "status" : "alert"}>{result.text}</p>}
      <button className="btn btn-primary" type="submit" disabled={pending || !current || next.length < 10 || next !== again}>
        {pending ? "바꾸는 중…" : "비밀번호 바꾸기"}
      </button>
    </form>
  );
}

export function ForcedPasswordPage({ user }: { user: AuthUser }) {
  return (
    <div className={s.wrap}>
      <PasswordForm user={user} forced />
    </div>
  );
}
