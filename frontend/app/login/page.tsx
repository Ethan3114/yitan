"use client";
import { FormEvent, use, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { api, ApiError, messageFor, read } from "@/lib/api";
import { profilesSchema } from "@/lib/models";
import { Shell, Status } from "@/components/shell";
import { BookCover } from "@/components/book-cover";
import { useHydrated } from "@/lib/use-hydrated";

export default function LoginPage({ searchParams }: { searchParams: Promise<{ for?: string; next?: string }> }) {
  const query = use(searchParams);
  const returnTo = query.next === "/admin" ? "/admin" : undefined;
  const recordFor = ["self", "mother", "father", "family"].includes(query.for ?? "") ? query.for : undefined;
  const router = useRouter();
  const hydrated = useHydrated();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [localAvailable, setLocalAvailable] = useState(false);
  const [localMode, setLocalMode] = useState(false);
  const [checking, setChecking] = useState(Boolean(recordFor));
  useEffect(() => { api<{ local_invite_login: boolean }>("/api/v1/auth/methods").then(value => setLocalAvailable(value.local_invite_login)).catch(cause => setMessage(messageFor(cause))); }, []);
  useEffect(() => {
    if (!recordFor) return;
    const controller = new AbortController();
    read("/api/v1/profiles", profilesSchema, controller.signal).then(() => {
      if (!controller.signal.aborted) router.replace(`/profiles?new=1&for=${recordFor}`);
    }).catch(cause => {
      if (controller.signal.aborted) return;
      if (!(cause instanceof ApiError && cause.code === "unauthorized")) setMessage(messageFor(cause));
      setChecking(false);
    });
    return () => controller.abort();
  }, [recordFor, router]);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setBusy(true); setMessage("");
    const form = new FormData(event.currentTarget);
    const body = localMode ? { code: String(form.get("code")) } : { username: String(form.get("username")), password: String(form.get("password")) };
    try { await api(localMode ? "/api/v1/auth/login" : "/api/v1/auth/password-login", { method: "POST", body: JSON.stringify(body) }); router.push(returnTo ?? (recordFor ? `/profiles?new=1&for=${recordFor}` : "/")); }
    catch (error) { setMessage(messageFor(error)); setBusy(false); }
  }
  return <Shell title="忆谈" subtitle="把日子，慢慢讲成一本书" nav={false}>
    <div className="login-intro"><div className="book-stage"><BookCover title="一生的故事" name="您" /></div><h2>那些平凡的日子，<br />值得被记住。</h2><p>您慢慢讲，我们认真听。<br />把一生的回忆，留给在乎的人。</p></div>
    {checking ? <Status loading /> : <form className="form" method="post" onSubmit={submit}>{localMode ? <label className="field">本机邀请码<input name="code" type="password" autoComplete="off" required maxLength={80} /></label> : <><label className="field">账号<input name="username" autoComplete="username" autoCapitalize="none" required minLength={3} maxLength={64} pattern="[a-zA-Z0-9_.-]+" placeholder="填写注册时的账号" /></label><label className="field">密码<input name="password" type="password" autoComplete="current-password" required minLength={12} maxLength={128} /></label></>}<button className="primary" disabled={busy || !hydrated}>{busy ? "正在进入…" : "翻开我的回忆录"}<ArrowRight size={20} /></button><Link href={recordFor ? `/register?for=${recordFor}` : "/register"}>第一次使用？持邀请码注册</Link><Link href="/recover">忘记密码</Link>{localAvailable ? <button className="text-link" type="button" onClick={() => setLocalMode(!localMode)}>{localMode ? "使用账号密码" : "进入已有本机家庭"}</button> : null}</form>}<Status error={message} />
  </Shell>;
}
