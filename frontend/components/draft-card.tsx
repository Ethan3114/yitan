"use client";
import { useEffect, useRef, useState } from "react";
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
  useEffect(() => {
    if (!active || !taskId) return;
    const controller = new AbortController();
    let delay = 2000;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      if (!document.hidden) {
        try {
          const next = await read(`/api/v1/draft-tasks/${taskId}`, taskSchema, controller.signal);
          setTask(next);
          if (next.status === "failed") requestId.current = "";
          if (next.status === "succeeded") { onComplete(); return; }
          if (next.status === "failed") return;
        } catch (cause) { if (!controller.signal.aborted) setError(messageFor(cause)); }
      }
      delay = Math.min(delay + 1000, 10000);
      if (!controller.signal.aborted) timer = setTimeout(poll, delay);
    }
    timer = setTimeout(poll, delay);
    return () => { controller.abort(); clearTimeout(timer); };
  }, [active, taskId, onComplete]);
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
