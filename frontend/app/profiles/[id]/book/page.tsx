"use client";
import { use } from "react";
import { DigitalBook } from "@/components/digital-book";
import { Shell, Status } from "@/components/shell";
import { profileSchema } from "@/lib/models";
import { useResource } from "@/lib/use-resource";

export default function PersonalBook({ params, searchParams }: { params: Promise<{ id: string }>; searchParams: Promise<{ view?: string; chapter?: string }> }) {
  const { id } = use(params);
  const query = use(searchParams);
  const { data, error, unauthorized, reload } = useResource(`/api/v1/profiles/${id}`, profileSchema);
  if (!data) return <Shell title="我的回忆录" back={`/profiles/${id}`} nav={false}><Status error={error} unauthorized={unauthorized} retry={reload} loading={!error} /></Shell>;
  const chapters = data.calls.slice().reverse().flatMap(call => call.chapters.map(chapter => ({ title: chapter.title, paragraphs: chapter.paragraphs.map(p => p.text) })));
  return <DigitalBook book={{ title: data.book_title, author: data.elder_name, chapters }} base={`/profiles/${id}/book`} back={`/profiles/${id}`} view={query.view} chapter={Number(query.chapter ?? 0)} />;
}
