"use client";
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { v4 as uuid } from "uuid";
import { Shell } from "@/components/shell";
import { api, messageFor } from "@/lib/api";
import { AccessNote } from "@/components/access-note";
export default function Join() {
  const router = useRouter();
  const token = useRef("");
  const requestId = useRef(uuid());
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { if (window.location.hash) { token.current = window.location.hash.slice(1); window.history.replaceState(null, "", "/join"); } }, []);
  return <Shell title="家人的邀请" nav={false}><section className="login-intro"><h2>有些故事，想讲给您听</h2><p>留下家人熟悉的称呼，一起读这本回忆录。</p></section><form className="card form" onSubmit={async e => { e.preventDefault(); setBusy(true); setError(""); try { if (!token.current) { setError("请重新打开家人发来的完整邀请地址。"); return; } await api("/api/v1/invitations/redeem", { method: "POST", body: JSON.stringify({ token: token.current, name, request_id: requestId.current }) }); router.replace("/family"); } catch (cause) { setError(messageFor(cause)); } finally { setBusy(false); } }}><label className="field">您的称呼<input required maxLength={40} value={name} onChange={e => setName(e.target.value)} placeholder="例如：小芸" autoComplete="nickname" /></label>{error ? <p role="alert">{error}</p> : null}<button className="primary" disabled={busy || !name.trim()}>加入并阅读</button><p className="small-text muted"><AccessNote /></p></form></Shell>;
}
