"use client";
import { use } from "react";
import { useRouter } from "next/navigation";
import Image from "next/image";
import { Shell, Status } from "@/components/shell";
import { CallPanel } from "@/components/call-panel";
import { profileSchema, meSchema } from "@/lib/models";
import { useResource } from "@/lib/use-resource";
import { API_BASE } from "@/lib/api";
export default function CallPage({ params: paramsPromise, searchParams: searchPromise }: { params: Promise<{ id: string }>; searchParams: Promise<{ photo?: string }> }) {
  const params = use(paramsPromise);
  const searchParams = use(searchPromise);
  const router = useRouter();
  const profile = useResource(`/api/v1/profiles/${params.id}`, profileSchema);
  const me = useResource("/api/v1/me", meSchema);
  return <Shell title="慢慢聊，不着急" back={`/profiles/${params.id}`} nav={false}><Status error={profile.error || me.error} unauthorized={profile.unauthorized || me.unauthorized} retry={() => { profile.reload(); me.reload(); }} loading={(!profile.data || !me.data) && !profile.error && !me.error} />{profile.data && me.data ? <>
    {searchParams.photo ? <Image unoptimized className="card" style={{ width: "100%", height: 200, objectFit: "contain", padding: 8 }} width={400} height={200} alt="本次访谈选择的老照片" src={`${API_BASE}/api/v1/photos/${searchParams.photo}`} /> : null}
    <CallPanel profileId={params.id} elderName={profile.data.elder_name} photoId={searchParams.photo} voiceReady={me.data.voice_ready} onFinished={() => router.push(`/profiles/${params.id}#contents`)} />
  </> : null}</Shell>;
}
