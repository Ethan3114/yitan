"use client";
import { use, useRef, useState } from "react";
import { v4 as uuid } from "uuid";
import { z } from "zod";
import { Shell, Status } from "@/components/shell";
import { SharedBookPreview } from "@/components/shared-book";
import { api, messageFor } from "@/lib/api";
import { managementSchema, previewSchema } from "@/lib/family";
import { photosSchema, profileSchema, dateLabel } from "@/lib/models";
import { useResource } from "@/lib/use-resource";
import { AccessNote } from "@/components/access-note";
export default function Family({ params: promise }: { params: Promise<{ id: string }> }) {
  const { id } = use(promise);
  const profile = useResource(`/api/v1/profiles/${id}`, profileSchema);
  const photos = useResource(`/api/v1/profiles/${id}/photos`, photosSchema);
  const family = useResource(`/api/v1/profiles/${id}/family`, managementSchema);
  const [chapters, setChapters] = useState<string[]>([]);
  const [photoIds, setPhotos] = useState<string[]>([]);
  const [audio, setAudio] = useState(false);
  const [inviteAudio, setInviteAudio] = useState(false);
  const [preview, setPreview] = useState<z.infer<typeof previewSchema> | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [inviteUrl, setInviteUrl] = useState("");
  const requestId = useRef(uuid());
  const inviteId = useRef(uuid());
  const selection = { chapter_ids: chapters, photo_ids: photoIds, allow_audio: audio };
  async function run(action: () => Promise<void>) {
    setBusy(true); setMessage("");
    try { await action(); } catch (cause) { setMessage(messageFor(cause)); } finally { setBusy(false); }
  }
  function toggle(value: string, selected: string[], setter: (value: string[]) => void) {
    setter(selected.includes(value) ? selected.filter(i => i !== value) : [...selected, value]); setPreview(null); requestId.current = uuid();
  }
  async function remove(path: string, prompt: string) {
    if (!window.confirm(prompt)) return;
    await run(async () => { await api(path, { method: "DELETE" }); family.reload(); });
  }
  return <Shell title="家人" subtitle="让在乎的人，读懂您的故事" profileId={id} active="family">
    <Status error={profile.error || family.error || photos.error || message} loading={!profile.data && !profile.error} retry={() => { profile.reload(); family.reload(); photos.reload(); }} />
    {family.data ? <div className="card"><h2>{family.data.publication ? "家人正在读这本书" : "挑一段故事，给家人看"}</h2><p className="muted small-text section">{family.data.publication ? `已展示 ${family.data.publication.chapters.length} 章。正文更新后，需要再次确认展示。` : "选择章节与照片，确认后再邀请家人。"}</p>{family.data.publication ? <><details><summary>看看家人能读到什么</summary><SharedBookPreview book={family.data.publication} /></details><button className="text-link" disabled={busy} onClick={() => void remove(`/api/v1/profiles/${id}/family/publication`, "取消家人阅读？已经保存的正文和留言仍会保留。")}>取消展示</button></> : null}</div> : null}
    {profile.data ? <section className="section"><h2>选择展示内容</h2><p className="small-text muted">原话和核对事项只由管理者查看。</p>
      {profile.data.calls.flatMap(c => c.chapters).map(c => <label className="consent" key={c.id}><input type="checkbox" checked={chapters.includes(c.id)} onChange={() => toggle(c.id, chapters, setChapters)} />{c.title}</label>)}
      {!profile.data.calls.some(c => c.chapters.length) ? <p className="notice">写成第一段回忆后，就能在这里分享。</p> : null}
      {photos.data?.items.map(p => <label className="consent" key={p.id}><input type="checkbox" checked={photoIds.includes(p.id)} onChange={() => toggle(p.id, photoIds, setPhotos)} />照片：{p.name}</label>)}
      <label className="consent"><input type="checkbox" checked={audio} onChange={e => { setAudio(e.target.checked); setPreview(null); requestId.current = uuid(); }} /><span>允许分享完整访谈原声<small className="muted" style={{ display: "block" }}>录音包含本次访谈其他内容；每次邀请还需单独授权。</small></span></label>
      <button className="secondary" disabled={busy || !chapters.length} onClick={() => void run(async () => setPreview(previewSchema.parse(await api(`/api/v1/profiles/${id}/family/preview`, { method: "POST", body: JSON.stringify(selection) }))))}>预览给家人看的内容</button>
      {preview ? <div className="card section"><SharedBookPreview book={preview.book} /><button className="primary" disabled={busy} onClick={() => void run(async () => { await api(`/api/v1/profiles/${id}/family/publish`, { method: "POST", body: JSON.stringify({ ...selection, digest: preview.digest, request_id: requestId.current }) }); setPreview(null); requestId.current = uuid(); family.reload(); })}>确认展示这些内容</button></div> : null}
    </section> : null}
    <section className="card section"><h2>邀请家人</h2><p className="small-text muted section"><AccessNote />每份邀请供一位家人使用，7 天内有效。</p>
      <label className="consent"><input type="checkbox" checked={inviteAudio} disabled={!family.data?.publication?.allow_audio} onChange={e => { setInviteAudio(e.target.checked); inviteId.current = uuid(); }} />允许这位家人听原声</label>
      <button className="primary" disabled={busy || !family.data?.publication} onClick={() => void run(async () => {
        if (["localhost", "127.0.0.1"].includes(window.location.hostname)) { setMessage("请从手机可访问的局域网地址打开本页，再创建邀请。"); return; }
        const result = z.object({ token: z.string() }).parse(await api(`/api/v1/profiles/${id}/invitations`, { method: "POST", body: JSON.stringify({ request_id: inviteId.current, allow_audio: inviteAudio }) }));
        setInviteUrl(`${window.location.origin}/join#${result.token}`); inviteId.current = uuid(); family.reload();
      })}>生成家人邀请</button>
      {inviteUrl ? <label className="field section">邀请地址<textarea readOnly value={inviteUrl} onFocus={e => e.target.select()} /><span className="small-text muted">长按全选并复制，发给这位家人。</span></label> : null}
      {family.data?.invitations.map(i => <div key={i.id} className="facts"><p>等待家人加入 · {dateLabel(i.expires_at)} 到期</p><button className="text-link" disabled={busy} onClick={() => void remove(`/api/v1/invitations/${i.id}`, "撤销这份邀请？")}>撤销邀请</button></div>)}
    </section>
    <section className="section"><h2>谁能看这本书</h2>{family.data?.members.map(m => <div key={m.id} className="card section"><h3>{m.name}</h3><p className="small-text muted">{m.allow_audio ? "可阅读，原声按展示授权" : "阅读文字与照片"}</p><button className="text-link" disabled={busy} onClick={() => void remove(`/api/v1/profiles/${id}/members/${m.id}`, `移除${m.name}的阅读权限？`)}>移除阅读权限</button></div>)}{family.data && !family.data.members.length ? <p className="muted section">还没有家人加入。</p> : null}</section>
    <section className="section"><h2>家人的留言</h2>{family.data?.comments.map(c => <div className="card section" key={c.id}><strong>{c.name}</strong><p>{c.text}</p><button className="text-link" disabled={busy} onClick={() => void remove(`/api/v1/family/comments/${c.id}`, "删除这条留言？")}>删除留言</button></div>)}</section>
  </Shell>;
}
