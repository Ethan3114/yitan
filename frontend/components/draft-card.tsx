"use client";
import { useEffect, useEffectEvent, useRef, useState } from "react";
import { v4 as uuid } from "uuid";
import { api, messageFor, read } from "@/lib/api";
import { Call, DraftTask, dateLabel, taskSchema } from "@/lib/models";
import { Status } from "./shell";

export function DraftCard({ call, onComplete }: { call: Call; onComplete: () => void }) {
  const [task, setTask] = useState<DraftTask | null>(call.task);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const requestId = useRef("");
  const active = task?.status === "queued" || task?.status === "running";
  const taskId = task?.id;
  const completeFromPoll = useEffectEvent(onComplete);
  useEffect(() => {
    if (!active || !taskId) return;
    let controller: AbortController | undefined;
    let timer: number | undefined;
    let disposed = false;
    let finished = false;
    let inFlight = false;
    let refreshWhenReady = false;
    function schedule() {
      if (!disposed && !finished && !document.hidden) timer = window.setTimeout(() => { void poll(); }, 30000);
    }
    async function poll() {
      if (disposed || finished || document.hidden) return;
      if (inFlight) { refreshWhenReady = true; return; }
      refreshWhenReady = false;
      inFlight = true;
      const request = new AbortController();
      controller = request;
      try {
        const next = await read(`/api/v1/draft-tasks/${taskId}`, taskSchema, request.signal);
        if (disposed || request.signal.aborted) return;
        setTask(next);
        setError("");
        finished = next.status === "succeeded" || next.status === "failed";
        if (next.status === "failed") requestId.current = "";
        if (next.status === "succeeded") completeFromPoll();
      } catch (cause) {
        if (!disposed && !request.signal.aborted) setError(messageFor(cause));
      } finally {
        inFlight = false;
        controller = undefined;
        if (!disposed && !finished && !document.hidden) {
          if (refreshWhenReady) void poll();
          else schedule();
        }
      }
    }
    function visibility() {
      window.clearTimeout(timer);
      if (document.hidden) controller?.abort();
      else { refreshWhenReady = true; void poll(); }
    }
    schedule();
    document.addEventListener("visibilitychange", visibility);
    return () => {
      disposed = true;
      window.clearTimeout(timer);
      controller?.abort();
      document.removeEventListener("visibilitychange", visibility);
    };
  }, [active, taskId]);
  async function start() {
    if (busy || active) return;
    if (call.chapters.length && !window.confirm("重新整理将替换这次访谈的章节与核对内容。是否继续？")) return;
    setBusy(true); setError("");
    if (!requestId.current) requestId.current = uuid();
    try {
      const next = taskSchema.parse(await api(`/api/v1/calls/${call.id}/draft-tasks`, { method: "POST", body: JSON.stringify({ request_id: requestId.current, replace: call.chapters.length > 0 }) }));
      setTask(next); if (next.status === "failed") requestId.current = "";
      if (next.status === "succeeded") onComplete();
    } catch (cause) { setError(messageFor(cause)); }
    finally { setBusy(false); }
  }
  return <div className="task-card"><span className="eyebrow">{dateLabel(call.created_at)} · 访谈</span>
    <h3>{active ? task?.step === "transcribing" ? "正在整理录音里的话" : "正在写成一段回忆" : task?.status === "failed" ? "这次整理还没有完成" : call.chapters.length ? "这次访谈已写成章节" : "让这段回忆成为书里的一页"}</h3>
    <p>{active ? "您可以离开这个页面，回来后继续查看。" : task?.status === "failed" ? "已保存的原话与章节仍在，可以重新整理。" : "按照您讲述的经历整理，每段都能查看原话。"}</p>
    <Status error={error} />
    {!active ? <button className="secondary" type="button" disabled={busy} onClick={start}>{busy ? "正在提交…" : task?.status === "failed" ? "重新整理" : call.chapters.length ? "按原话重新整理" : "写成一段回忆"}</button> : <p role="status">整理进行中</p>}
  </div>;
}
