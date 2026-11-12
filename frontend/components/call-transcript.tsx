"use client";

import { useEffect, useId, useRef, useState } from "react";
import { ArrowDown, Type } from "lucide-react";
import type { CaptionEntry } from "@/lib/call-captions";
import styles from "./call-transcript.module.css";

const LABELS: Record<CaptionEntry["status"], string> = {
  recognizing: "正在听写", confirmed: "本句结束", failed: "这句没有识别完整",
  queued: "准备播放", speaking: "正在说", spoken: "已说完", interrupted: "已打断",
};

export function CallTranscript({ entries, paused }: { entries: readonly CaptionEntry[]; paused: boolean }) {
  const [large, setLarge] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [following, setFollowing] = useState(true);
  const follow = useRef(true);
  const scroller = useRef<HTMLDivElement>(null);
  const id = useId();
  const shown = expanded ? entries : entries.slice(-2);

  useEffect(() => {
    if (follow.current && scroller.current) scroller.current.scrollTop = scroller.current.scrollHeight;
  }, [entries, expanded, large]);

  function latest() {
    follow.current = true;
    setFollowing(true);
    if (scroller.current) scroller.current.scrollTop = scroller.current.scrollHeight;
  }

  return <section className={styles.transcript} data-large={large} data-expanded={expanded} aria-label="实时对话文字">
    <div className={styles.toolbar}>
      <h3>对话文字</h3>
      <button type="button" aria-pressed={large} className={styles.sizeButton} onClick={() => setLarge(value => !value)}><Type size={18} aria-hidden="true" />大字</button>
      <button type="button" className={styles.historyButton} aria-expanded={expanded} aria-controls={id} onClick={() => { follow.current = true; setFollowing(true); setExpanded(value => !value); }}>{expanded ? "收起对话" : "完整对话"}</button>
    </div>
    <div id={id} ref={scroller} className={styles.messages} role="log" aria-label="访谈对话" aria-live="off" tabIndex={0} onScroll={() => {
      const element = scroller.current;
      if (!element) return;
      const nearBottom = element.scrollHeight - element.scrollTop - element.clientHeight < 48;
      follow.current = nearBottom;
      setFollowing(nearBottom);
    }}>
      {shown.length ? shown.map(entry => <article key={entry.id} className={styles.message} data-role={entry.role} data-caption-status={entry.status} data-caption-source={entry.source}>
        <header><strong>{entry.role === "user" ? "您" : "忆谈"}</strong><span>{paused && entry.role === "assistant" && entry.status === "speaking" ? "声音已暂停" : entry.reference && entry.status === "confirmed" ? "发言已结束" : LABELS[entry.status]}</span></header>
        <p>{entry.text || (entry.role === "user" ? "正在听您讲……" : "正在播放这段声音……")}</p>
        {entry.reference ? <small>参考文字，以实际听到的声音为准{entry.status === "interrupted" ? "；其中可能有尚未播放的内容" : ""}。</small> : null}
      </article>) : <p className={styles.empty}>接通后，您和忆谈的话会显示在这里。</p>}
    </div>
    {!following && expanded ? <button type="button" className={styles.latest} onClick={latest}><ArrowDown size={16} aria-hidden="true" />回到最新一句</button> : null}
    <p className={styles.note}>实时文字可能有听写误差。</p>
  </section>;
}
