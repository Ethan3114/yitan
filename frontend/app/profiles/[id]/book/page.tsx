"use client";
import { use, useEffect } from "react";
import { DigitalBook } from "@/components/digital-book";
import { Shell, Status } from "@/components/shell";
import { profileSchema } from "@/lib/models";
import { useResource } from "@/lib/use-resource";
import { api } from "@/lib/api";

export default function PersonalBook({ params, searchParams }: { params: Promise<{ id: string }>; searchParams: Promise<{ view?: string; chapter?: string }> }) {
  const { id } = use(params);
  const query = use(searchParams);
  const { data, error, unauthorized, reload } = useResource(`/api/v1/profiles/${id}`, profileSchema);
  const ordered = data?.calls.slice().reverse().flatMap(call => call.chapters) ?? [];
  const requested = Number(query.chapter ?? 0);
  const selected = Number.isInteger(requested) && requested >= 0 && requested < ordered.length ? requested : 0;
  const readingId = query.view === "read" ? ordered[selected]?.id : undefined;
  useEffect(() => {
    if (!readingId) return;
    void api(`/api/v1/chapters/${readingId}/read`, { method: "POST" }).catch(() => {
      sessionStorage.setItem("yitan.analyticsFailures", String(Number(sessionStorage.getItem("yitan.analyticsFailures") || "0") + 1));
    });
  }, [readingId]);
  if (!data) return <Shell title="我的回忆录" back={`/profiles/${id}`} nav={false}><Status error={error} unauthorized={unauthorized} retry={reload} loading={!error} /></Shell>;
  const chapters = data.calls.slice().reverse().flatMap(call => call.chapters.map(chapter => ({ title: chapter.title, paragraphs: chapter.paragraphs.map(p => p.text) })));
  return <DigitalBook book={{ title: data.book_title, author: data.elder_name, chapters }} base={`/profiles/${id}/book`} back={`/profiles/${id}`} view={query.view} chapter={Number(query.chapter ?? 0)} />;
}
