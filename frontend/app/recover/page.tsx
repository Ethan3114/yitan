"use client";
import { FormEvent, useState } from "react";
import Link from "next/link";
import { Shell, Status } from "@/components/shell";
import { api, messageFor } from "@/lib/api";
import { useHydrated } from "@/lib/use-hydrated";

export default function Recover() {
  const hydrated = useHydrated();
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [message, setMessage] = useState("");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const form = new FormData(event.currentTarget);
    if (form.get("password") !== form.get("confirm")) { setMessage("两次填写的密码不一致"); return; }
    setBusy(true); setMessage("");
    try { await api("/api/v1/auth/recover", { method: "POST", body: JSON.stringify({ token: form.get("token"), password: form.get("password") }) }); setDone(true); }
    catch (cause) { setMessage(messageFor(cause)); }
    finally { setBusy(false); }
  }
  return <Shell title="找回家庭账户" subtitle="重设后，请使用新密码登录" nav={false} back="/login">{done ? <div className="card"><h2>密码已重设</h2><p>之前登录的设备已退出。</p><Link className="primary" href="/login">重新登录</Link></div> : <><p className="notice">请联系给您邀请码的管理员。核实账户后，管理员会提供一次性恢复码，三十分钟内有效。</p><form className="form" method="post" onSubmit={submit}>
    <label className="field">恢复码<input name="token" autoComplete="off" required minLength={20} maxLength={128} /></label>
    <label className="field">新密码<input name="password" type="password" autoComplete="new-password" required minLength={12} maxLength={128} /></label>
    <label className="field">再次填写新密码<input name="confirm" type="password" autoComplete="new-password" required minLength={12} maxLength={128} /></label>
    <button className="primary" disabled={busy || !hydrated}>{busy ? "正在保存…" : "保存新密码"}</button>
  </form></>}<Status error={message} /></Shell>;
}
