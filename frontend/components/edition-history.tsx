"use client";
import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { v4 as uuid } from "uuid";
import { z } from "zod";
import { api, messageFor } from "@/lib/api";
const editionsSchema = z.object({ items: z.array(z.object({ id: z.string(), created_at: z.string(), chapters: z.array(z.object({ title: z.string(), paragraphs: z.array(z.object({ text: z.string() })) })) })) });
export function EditionHistory({ callId, profileId }: { callId: string; profileId: string }) {
  const router = useRouter();
  const [items, setItems] = useState<z.infer<typeof editionsSchema>["items"] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const requestId = useRef(uuid());
  return <details className="section"><summary>查看这次访谈的历史正文</summary><p className="small-text muted">恢复会更换这次访谈的全部章节。当前正文另存为历史，原话、核对记录和家人展示保持原样。</p><button className="text-link" disabled={busy} onClick={async () => { setBusy(true); setError(""); try { setItems(editionsSchema.parse(await api(`/api/v1/calls/${callId}/editions`)).items); } catch (cause) { setError(messageFor(cause)); } finally { setBusy(false); } }}>读取历史版本</button>{error ? <p role="alert">{error}</p> : null}{items?.length === 0 ? <p>还没有较早的正文。</p> : null}{items?.map(item => <details key={item.id} className="card section"><summary>{item.chapters.length} 章 · {item.created_at.slice(0, 10)} 保存</summary>{item.chapters.map((c, i) => <article key={i}><h3>{c.title}</h3>{c.paragraphs.map((p, n) => <p className="reading-paragraph" key={n}>{p.text}</p>)}</article>)}<button className="secondary" disabled={busy} onClick={async () => { if (!window.confirm("恢复这个版本的全部章节？当前正文会保存到历史中。")) return; setBusy(true); setError(""); try { await api(`/api/v1/calls/${callId}/editions/${item.id}/restore`, { method: "POST", body: JSON.stringify({ confirm: true, request_id: requestId.current }) }); router.replace(`/profiles/${profileId}`); } catch (cause) { setError(messageFor(cause)); } finally { setBusy(false); } }}>恢复这个版本</button></details>)}</details>;
}
