"use client";
import { FormEvent, use, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowRight } from "lucide-react";
import { api, ApiError, messageFor, read } from "@/lib/api";
import { profilesSchema } from "@/lib/models";
import { Shell, Status } from "@/components/shell";
import { BookCover } from "@/components/book-cover";

export default function LoginPage({ searchParams }: { searchParams: Promise<{ for?: string }> }) {
  const query = use(searchParams);
  const recordFor = ["self", "mother", "father", "family"].includes(query.for ?? "") ? query.for : undefined;
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [checking, setChecking] = useState(Boolean(recordFor));
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
    const code = String(new FormData(event.currentTarget).get("code"));
    try { await api("/api/v1/auth/login", { method: "POST", body: JSON.stringify({ code }) }); router.push(recordFor ? `/profiles?new=1&for=${recordFor}` : "/"); }
    catch (error) { setMessage(messageFor(error)); setBusy(false); }
  }
  return <Shell title="忆谈" subtitle="把日子，慢慢讲成一本书" nav={false}>
    <div className="login-intro"><div className="book-stage"><BookCover title="一生的故事" name="您" /></div><h2>那些平凡的日子，<br />值得被记住。</h2><p>您慢慢讲，我们认真听。<br />把一生的回忆，留给在乎的人。</p></div>
    {checking ? <Status loading /> : <form className="form" onSubmit={submit}><label className="field">邀请码<input name="code" type="password" autoComplete="off" required maxLength={80} placeholder="填写您的邀请码" /></label><button className="primary" disabled={busy}>{busy ? "正在进入…" : "翻开我的回忆录"}<ArrowRight size={20} /></button></form>}<Status error={message} />
  </Shell>;
}
