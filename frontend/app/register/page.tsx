"use client";
import { FormEvent, use, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Shell, Status } from "@/components/shell";
import { api, messageFor } from "@/lib/api";
import { track } from "@/lib/analytics";
import { useHydrated } from "@/lib/use-hydrated";

export default function Register({ searchParams }: { searchParams: Promise<{ for?: string }> }) {
  const query = use(searchParams);
  const recordFor = ["self", "mother", "father", "family"].includes(query.for ?? "") ? query.for : undefined;
  const router = useRouter();
  const hydrated = useHydrated();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  useEffect(() => { track("registration_viewed"); }, []);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const form = new FormData(event.currentTarget);
    if (form.get("password") !== form.get("confirm")) { setMessage("两次填写的密码不一致"); return; }
    setBusy(true); setMessage("");
    track("registration_submitted");
    try {
      await api("/api/v1/auth/register", { method: "POST", body: JSON.stringify({ username: form.get("username"), password: form.get("password"), invitation: form.get("invitation") }) });
      router.replace(recordFor ? `/profiles?new=1&for=${recordFor}` : "/profiles?new=1");
    } catch (cause) { setMessage(messageFor(cause)); setBusy(false); }
  }
  return <Shell title="建立家庭账户" subtitle="为家里的故事，留一个安心的地方" nav={false} back="/login"><form className="form" method="post" onSubmit={submit}>
    <label className="field">邀请码<input name="invitation" autoComplete="off" required minLength={20} maxLength={128} placeholder="填写邀请人给您的邀请码" /></label>
    <label className="field">账号<input name="username" autoComplete="username" autoCapitalize="none" required minLength={3} maxLength={64} pattern="[a-zA-Z0-9_.-]+" placeholder="至少三个英文字母或数字，可含 _ . -" /></label>
    <label className="field">密码<input name="password" type="password" autoComplete="new-password" required minLength={12} maxLength={128} placeholder="至少十二个字符" /></label>
    <label className="field">再次填写密码<input name="confirm" type="password" autoComplete="new-password" required minLength={12} maxLength={128} /></label>
    <p className="muted small-text">注册后，您将拥有独立的家庭空间。回忆录和录音由您决定向哪些家人展示。</p>
    <button className="primary" disabled={busy || !hydrated}>{busy ? "正在建立账户…" : "注册并开始记录"}</button><Link href="/login">已有账户，前往登录</Link>
  </form><Status error={message} /></Shell>;
}
