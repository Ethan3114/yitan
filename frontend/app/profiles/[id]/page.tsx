"use client";
import Link from "next/link";
import { FormEvent, use, useEffect, useState } from "react";
import { BookOpen, ChevronRight, Images, Mic, PencilLine } from "lucide-react";
import { Shell, Status, Empty } from "@/components/shell";
import { BookCover } from "@/components/book-cover";
import { PhotoAlbum } from "@/components/photo-album";
import { DraftCard } from "@/components/draft-card";
import { CallRecording } from "@/components/call-recording";
import { profileSchema, dateLabel } from "@/lib/models";
import { useResource } from "@/lib/use-resource";
import { api, messageFor } from "@/lib/api";

export default function ProfilePage({ params: paramsPromise, searchParams: searchPromise }: { params: Promise<{ id: string }>; searchParams: Promise<{ tab?: string }> }) {
  const params = use(paramsPromise);
  const searchParams = use(searchPromise);
  const { data: profile, error, unauthorized, reload } = useResource(`/api/v1/profiles/${params.id}`, profileSchema);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const album = searchParams.tab === "photos";
  useEffect(() => { if (profile) localStorage.setItem("yitan.currentProfile", profile.id); }, [profile]);
  const base = `/profiles/${params.id}`;
  async function saveTitle(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setSaving(true);
    const title = new FormData(event.currentTarget).get("title");
    try { await api(`/api/v1/profiles/${params.id}/book`, { method: "PUT", body: JSON.stringify({ title }) }); setEditing(false); reload(); }
    catch (cause) { setMessage(messageFor(cause)); }
    finally { setSaving(false); }
  }
  const chapters = profile?.calls.slice().reverse().flatMap(call => call.chapters.map(chapter => ({ ...chapter, date: call.created_at }))) ?? [];
  return <Shell title="我的回忆录" subtitle="说过的话，都有了归处" profileId={params.id}>
    <Status error={error} unauthorized={unauthorized} retry={reload} loading={!profile && !error} />
    {profile ? <>
      <section className="personal-book-front" aria-label="我的书封"><div className="book-stage"><BookCover title={profile.book_title} name={profile.elder_name} /></div>
      <div className="book-caption"><span>{chapters.length} 篇回忆</span><span>{profile.calls.length} 次访谈</span></div>
      {chapters.length ? <Link className="secondary section" href={`${base}/book`}>翻开我的回忆录</Link> : null}
      <div style={{ textAlign: "center" }}><button type="button" className="text-link small-text" onClick={() => setEditing(!editing)}><PencilLine size={14} style={{ display: "inline", marginRight: 6 }} />修改书名</button></div>
      </section>
      {editing ? <form className="card form" onSubmit={saveTitle}><label className="field">书名<input name="title" defaultValue={profile.book_title} required maxLength={60} /></label><button className="primary" disabled={saving}>保存书名</button></form> : null}
      <Status error={message} />
      <section className="talk-card"><span className="eyebrow">今天，慢慢聊</span><h2>{profile.calls.length ? "还有哪些往事，想接着讲？" : "小时候的家，是什么样子？"}</h2><Link className="primary" href={`${base}/call`}><Mic size={24} />{profile.calls.length ? "继续聊聊" : "开始第一次访谈"}</Link><Link href={`${base}?tab=photos`} className="photo-prompt"><Images size={20} />从照片聊起<ChevronRight size={18} /></Link></section>
      <section className="section" id="contents"><div className="segmented" aria-label="回忆录内容"><Link href={base} aria-current={!album ? "page" : undefined}>目录</Link><Link href={`${base}?tab=photos`} aria-current={album ? "page" : undefined}>照片</Link></div>
        {album ? <PhotoAlbum profileId={params.id} /> : <>
          <div className="section-heading"><h2>写下来的时光</h2><span className="small-text muted">共 {chapters.length} 篇</span></div>
          {chapters.length ? chapters.map((chapter, index) => <Link className="chapter-row" key={chapter.id} href={`${base}/chapters/${chapter.id}`}><span className="chapter-number">{String(index + 1).padStart(2, "0")}</span><div className="chapter-copy"><h3>{chapter.title}</h3><p>{chapter.paragraphs[0]?.text}</p><small>{dateLabel(chapter.date)} 记下</small></div><ChevronRight size={18} style={{ flexShrink: 0, marginTop: 8 }} /></Link>) : <Empty icon={<BookOpen size={40} />} title="第一章，等您来讲"><p>每一段亲口讲述的经历，<br />都会在这里慢慢成书。</p></Empty>}
          {profile.calls.filter(call => call.status !== "live" && (!call.chapters.length || call.task?.status === "running" || call.task?.status === "queued")).map(call => <DraftCard key={call.id} call={call} onComplete={reload} />)}
        </>}
      </section>
      <details className="section" id="history"><summary>访谈记录 · {profile.calls.length} 次</summary>{profile.calls.length ? profile.calls.map(call => <div key={call.id} className="task-card"><h3>{dateLabel(call.created_at)}的访谈</h3><p>{call.status === "live" ? "访谈尚未结束" : call.status === "failed" ? "访谈中断，已保存内容可查看" : "访谈已结束"}</p><CallRecording callId={call.id} kinds={call.audio_kinds} />{call.texts.full || call.texts.side ? <details><summary>查看原话</summary><p style={{ whiteSpace: "pre-wrap" }}>{call.texts.full || call.texts.side}</p></details> : null}{call.chapters.length ? <DraftCard call={call} onComplete={reload} /> : null}</div>) : <p className="muted">还没有开始访谈。</p>}</details>
      <Link className="text-link small-text" href="/profiles">切换回忆录</Link>
    </> : null}
  </Shell>;
}
