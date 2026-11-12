"use client";
import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";
import { api, messageFor } from "@/lib/api";
import { Profile } from "@/lib/models";
import { Status } from "./shell";
import { BookCover } from "./book-cover";
import { useHydrated } from "@/lib/use-hydrated";

export function ProfileForm({ profile, recordFor }: { profile?: Profile; recordFor?: string }) {
  const router = useRouter();
  const hydrated = useHydrated();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (busy) return;
    const data = new FormData(event.currentTarget);
    setBusy(true); setError("");
    const body = { elder_name: String(data.get("elder_name")).trim(), birth_year: data.get("birth_year") ? Number(data.get("birth_year")) : null, hometown: data.get("hometown"), family_names: data.get("family_names"), topics_want: data.get("topics_want"), topics_avoid: data.get("topics_avoid") };
    try { const saved = await api<{ id: string }>(profile ? `/api/v1/profiles/${profile.id}` : "/api/v1/profiles", { method: profile ? "PATCH" : "POST", body: JSON.stringify(body) }); localStorage.setItem("yitan.currentProfile", saved.id); router.push(`/profiles/${saved.id}${!profile && recordFor && recordFor !== "self" ? "/handoff" : ""}`); }
    catch (cause) { setError(messageFor(cause)); setBusy(false); }
  }
  return <form className="form" method="post" onSubmit={submit}>
    <div className="book-stage"><BookCover title="故事，从这里开始" /></div>
    <label className="field">{recordFor && recordFor !== "self" ? "怎么称呼这位家人" : "怎么称呼您"}<input name="elder_name" required maxLength={40} defaultValue={profile?.elder_name} placeholder="例如：李奶奶" /></label>
    <details className="profile-extras" open={profile ? true : undefined}><summary>补充背景与不想聊的话题（选填）</summary><div className="form">
    <label className="field">出生年份<small>可以不填，以您记得的为准</small><input name="birth_year" type="number" min={1900} max={2026} inputMode="numeric" defaultValue={profile?.birth_year ?? ""} placeholder="例如：1946" /></label>
    <label className="field">家乡<input name="hometown" maxLength={80} defaultValue={profile?.hometown} placeholder="您从哪里来" /></label>
    <label className="field">重要的家人<input name="family_names" maxLength={200} defaultValue={profile?.family_names} placeholder="称呼或名字" /></label>
    <label className="field">想聊的事<textarea name="topics_want" maxLength={500} defaultValue={profile?.topics_want} placeholder="童年的家、第一次工作、相识的故事…" /></label>
    <label className="field">希望避开的话题<small>访谈时会尊重您的意愿</small><textarea name="topics_avoid" maxLength={500} defaultValue={profile?.topics_avoid} placeholder="可以不填" /></label>
    </div></details>
    <Status error={error} /><button className="primary" disabled={busy || !hydrated}>{busy ? "正在保存…" : profile ? "保存资料" : "开始这本回忆录"}</button>
  </form>;
}
