"use client";
import { use, useEffect, useState } from "react";
import { Shell, Status } from "@/components/shell";
import { api, messageFor } from "@/lib/api";
import { CallRecording } from "@/components/call-recording";
import { readerSchema } from "@/lib/models";
import { useResource } from "@/lib/use-resource";
import { EditionHistory } from "@/components/edition-history";

export default function Reader({ params: paramsPromise }: { params: Promise<{ id: string; chapterId: string }> }) {
  const params = use(paramsPromise);
  const { data, error, unauthorized, reload } = useResource(`/api/v1/chapters/${params.chapterId}`, readerSchema);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [scores, setScores] = useState<Record<string, number>>({});
  const [details, setDetails] = useState<Record<string, string>>({});
  const chapterId = data?.chapter.id;
  useEffect(() => {
    if (!chapterId) return;
    void api(`/api/v1/chapters/${chapterId}/read`, { method: "POST" }).catch(() => {
      sessionStorage.setItem("yitan.analyticsFailures", String(Number(sessionStorage.getItem("yitan.analyticsFailures") || "0") + 1));
    });
  }, [chapterId]);
  async function fact(id: string, status: string) {
    setBusy(true); setMessage("");
    try { await api(`/api/v1/facts/${id}`, { method: "POST", body: JSON.stringify({ status, detail: details[id] ?? "" }) }); reload(); }
    catch (cause) { setMessage(messageFor(cause)); }
    finally { setBusy(false); }
  }
  async function score(id: string, value: number) {
    setBusy(true);
    try { await api(`/api/v1/paragraphs/${id}/score`, { method: "POST", body: JSON.stringify({ score: value }) }); setScores(current => ({ ...current, [id]: value })); }
    catch (cause) { setMessage(messageFor(cause)); }
    finally { setBusy(false); }
  }
  return <Shell title="读一段回忆" back={`/profiles/${params.id}#contents`} profileId={params.id}>
    <Status error={error || message} unauthorized={unauthorized} retry={reload} loading={!data && !error} />
    {data ? <article className="reader"><p className="eyebrow">{data.elder_name} · 亲口讲述</p><h2>{data.chapter.title}</h2>
      <div className="reading-body">{data.chapter.paragraphs.map(paragraph => <section key={paragraph.id} className="reading-paragraph"><p>{paragraph.text}</p><details><summary>看看这段原话</summary>{paragraph.source_quotes.length ? paragraph.source_quotes.map((ref, index) => <blockquote className="quote" key={`${ref.line}-${index}`}>{ref.quote}<small>原文第 {ref.line} 行</small></blockquote>) : <blockquote className="quote">{paragraph.source_quote}</blockquote>}<div className="button-row" aria-label="评价这段文字">{["需要修改", "基本符合", "写得很好"].map((label, index) => <button key={label} type="button" disabled={busy} aria-pressed={scores[paragraph.id] === index + 1} onClick={() => void score(paragraph.id, index + 1)}>{scores[paragraph.id] === index + 1 ? "已选：" : ""}{label}</button>)}</div></details></section>)}</div>
      {data.audio_kinds.length ? <details className="section"><summary>听听这次访谈的原声</summary><CallRecording callId={data.call_id} kinds={data.audio_kinds} /></details> : null}
      {data.chapter.facts.length ? <details className="section"><summary>需要核对的事 · {data.chapter.facts.length} 项</summary>{data.chapter.facts.map(item => <div className="facts" key={item.id}><h3>{item.topic}</h3><p>{item.detail}</p>{item.status === "open" ? <div className="button-row"><button type="button" disabled={busy} onClick={() => void fact(item.id, "confirmed")}>确认这件事</button><button type="button" disabled={busy} onClick={() => void fact(item.id, "rejected")}>这件事不采用</button></div> : <span className="tag">{item.status === "confirmed" ? "已确认" : "已排除"}</span>}</div>)}</details> : null}
      {data.chapter.facts.length ? <details className="section"><summary>补充或修改核对说明</summary>{data.chapter.facts.map(item => <div className="facts" key={item.id}><label className="field">{item.topic}<textarea maxLength={500} value={details[item.id] ?? item.detail} onChange={e => setDetails(current => ({ ...current, [item.id]: e.target.value }))} /></label><button className="text-link" disabled={busy} onClick={() => void fact(item.id, item.status)}>保存这条说明</button></div>)}</details> : null}
      <EditionHistory callId={data.call_id} profileId={params.id} />
      <p className="small-text muted section" style={{ textAlign: "center" }}>这一段记忆，已经好好留下。</p>
    </article> : null}
  </Shell>;
}
