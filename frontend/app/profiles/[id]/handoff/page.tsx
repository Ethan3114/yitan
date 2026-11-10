"use client";
import { use } from "react";
import Link from "next/link";
import { BookCover } from "@/components/book-cover";
import { Shell, Status } from "@/components/shell";
import { profileSchema } from "@/lib/models";
import { useResource } from "@/lib/use-resource";

export default function Handoff({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { data, error, unauthorized, reload } = useResource(`/api/v1/profiles/${id}`, profileSchema);
  return <Shell title="陪家人，开始第一页" nav={false} back={`/profiles/${id}`}><Status loading={!data && !error} error={error} unauthorized={unauthorized} retry={reload} />{data ? <><div className="book-stage"><BookCover title={data.book_title} name={data.elder_name} /></div><div className="call-heading"><h2>把手机交给{data.elder_name}</h2><p>找个安静的地方，陪在身边也可以。<br />由本人同意录音后，就能开始聊聊。</p></div><Link className="primary" href={`/profiles/${id}/call`}>准备好了，开始聊聊</Link><Link className="text-link" href={`/profiles/${id}`}>先看看这本回忆录</Link></> : null}</Shell>;
}
