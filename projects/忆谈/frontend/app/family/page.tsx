"use client";
import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { v4 as uuid } from "uuid";
import { Shell, Status } from "@/components/shell";
import { SharedBookPreview } from "@/components/shared-book";
import { api, messageFor } from "@/lib/api";
import { readingSchema } from "@/lib/family";
import { useResource } from "@/lib/use-resource";
export default function FamilyReader() {
  const router = useRouter();
  const { data, error, reload } = useResource("/api/v1/family/reading", readingSchema);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [chapter, setChapter] = useState("");
  const [text, setText] = useState("");
  const requestId = useRef(uuid());
  async function run(action: () => Promise<void>) { setBusy(true); setMessage(""); try { await action(); } catch (cause) { setMessage(messageFor(cause)); } finally { setBusy(false); } }
  return <Shell title="家人的回忆录" nav={false}>
    <Status error={error || message} retry={reload} loading={!data && !error} />
    {data ? <><p className="muted">{data.name}，欢迎回来。</p><button className="text-link" onClick={reload}>刷新阅读内容</button>
      {data.publication ? <><SharedBookPreview book={data.publication} family /><section className="section"><h2>读完想说的话</h2><form className="form section" onSubmit={e => { e.preventDefault(); void run(async () => { await api("/api/v1/family/comments", { method: "POST", body: JSON.stringify({ publication_id: data.publication!.id, chapter_id: chapter || data.publication!.chapters[0].id, text, request_id: requestId.current }) }); setText(""); requestId.current = uuid(); reload(); }); }}><label className="field">这条留言写给哪一章<select value={chapter || data.publication.chapters[0].id} onChange={e => { setChapter(e.target.value); requestId.current = uuid(); }}>{data.publication.chapters.map(c => <option key={c.id} value={c.id}>{c.title}</option>)}</select></label><label className="field">给家人留句话<textarea required maxLength={1000} value={text} onChange={e => { setText(e.target.value); requestId.current = uuid(); }} /></label><button className="primary" disabled={busy || !text.trim()}>留下这句话</button></form>
        {data.comments.map(c => <article key={c.id} className="card section"><strong>{c.name}</strong><p className="small-text muted">{data.publication!.chapters.find(ch => ch.id === c.chapter_id)?.title}</p><p style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{c.text}</p>{c.mine ? <button className="text-link" disabled={busy} onClick={() => { if (window.confirm("删除自己的这条留言？")) void run(async () => { await api(`/api/v1/family/comments/${c.id}`, { method: "DELETE" }); reload(); }); }}>删除我的留言</button> : null}</article>)}</section></> : <p className="notice">家人还在挑选故事，展示后可以在这里阅读。</p>}
      <button className="secondary section" disabled={busy} onClick={() => void run(async () => { await api("/api/v1/family/logout", { method: "POST" }); router.replace("/join"); })}>退出家人阅读</button></> : null}
  </Shell>;
}
