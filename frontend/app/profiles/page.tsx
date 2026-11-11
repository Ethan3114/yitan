"use client";
import { use } from "react";
import Link from "next/link";
import { Plus, BookOpen } from "lucide-react";
import { Shell, Status, Empty } from "@/components/shell";
import { BookCover } from "@/components/book-cover";
import { ProfileForm } from "@/components/profile-form";
import { profilesSchema } from "@/lib/models";
import { useResource } from "@/lib/use-resource";

export default function ProfilesPage({ searchParams: searchPromise }: { searchParams: Promise<{ new?: string; select?: string; for?: string }> }) {
  const searchParams = use(searchPromise);
  const { data, error, unauthorized, reload } = useResource("/api/v1/profiles", profilesSchema);
  const creating = searchParams.new === "1";
  return <Shell title={creating ? searchParams.for === "self" ? "开始我的回忆录" : "为家人记一本" : "家里的回忆录"} subtitle="每个人，都有值得留下的故事" back={creating ? "/welcome?step=5" : undefined}>
    <Status error={error} unauthorized={unauthorized} retry={reload} loading={!data && !error} />
    {data ? creating ? <ProfileForm recordFor={searchParams.for} /> : <>
      {searchParams.select ? <p className="notice">选择一本回忆录，查看家人页面。</p> : null}
      {data.items.length ? <div className="section">{data.items.map(item => <Link key={item.id} href={`/profiles/${item.id}${searchParams.select ? "/family" : ""}`} className="card list-card"><BookCover small title={`${item.elder_name}的故事`} name={item.elder_name} /><div><h2>{item.elder_name}的故事</h2><p>{item.hometown || "从这里，慢慢讲起"}</p><span className="text-link small-text">翻开回忆录 →</span></div></Link>)}</div> : <Empty icon={<BookOpen size={46} />} title="第一本回忆录，等您来讲"><p>记下称呼和一些背景，<br />就可以开始第一次访谈。</p></Empty>}
      <Link className="secondary" href="/profiles?new=1"><Plus size={22} />为家人记一本</Link>
    </> : null}
  </Shell>;
}
