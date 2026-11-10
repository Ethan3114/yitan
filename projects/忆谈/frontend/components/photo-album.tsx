"use client";
import Link from "next/link";
import Image from "next/image";
import { ImagePlus, Images } from "lucide-react";
import { useRef, useState } from "react";
import { API_BASE, apiForm, messageFor } from "@/lib/api";
import { photosSchema } from "@/lib/models";
import { useResource } from "@/lib/use-resource";
import { Empty, Status } from "./shell";

export function PhotoAlbum({ profileId }: { profileId: string }) {
  const { data, error, reload } = useResource(`/api/v1/profiles/${profileId}/photos`, photosSchema);
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  async function upload(file?: File) {
    if (!file) return;
    if (file.size > 10 * 1024 * 1024) { setMessage("照片不能超过 10 MB"); return; }
    setBusy(true); setMessage("");
    const body = new FormData(); body.append("file", file);
    try { await apiForm(`/api/v1/profiles/${profileId}/photos`, body); reload(); if (input.current) input.current.value = ""; }
    catch (cause) { setMessage(messageFor(cause)); }
    finally { setBusy(false); }
  }
  return <section>
    <p className="muted small-text" style={{ marginBottom: 18 }}>一张老照片，也能打开一段回忆。<br />支持 JPG、PNG、WebP，每张最多 10 MB。</p>
    <label className="secondary" style={{ cursor: busy ? "wait" : "pointer" }}><ImagePlus size={22} />{busy ? "正在保存照片…" : "添加老照片"}<input ref={input} aria-label="上传老照片" type="file" accept="image/jpeg,image/png,image/webp" className="sr-only" disabled={busy} onChange={event => void upload(event.target.files?.[0])} /></label>
    <Status error={error || message} retry={reload} loading={!data && !error} />
    {data?.items.length === 0 ? <Empty icon={<Images size={40} />} title="把老照片放在这里"><p>人物、地点和当年的故事，<br />留待您亲口告诉我们。</p></Empty> : null}
    <div className="photo-grid section">{data?.items.map(photo => <article key={photo.id} className="photo-tile"><a href={`${API_BASE}${photo.url}`} target="_blank" rel="noreferrer" aria-label={`查看照片 ${photo.name}`} style={{ padding: 0 }}><Image unoptimized width={300} height={300} src={`${API_BASE}${photo.url}`} alt={photo.name} /></a><Link href={`/profiles/${profileId}/call?photo=${photo.id}`}>从这张照片聊起</Link></article>)}</div>
  </section>;
}
