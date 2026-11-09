import { useAuth } from "../api/auth";
import { StateView } from "../components/Misc";
import { PasswordForm } from "./LoginPage";

export function AccountPage() {
  const auth = useAuth();
  if (auth.status !== "signed_in") return <StateView kind="empty" detail="로그인이 꺼진 배포에서는 계정 설정이 없습니다." />;
  return <PasswordForm user={auth.user} />;
}
