"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { BookCover } from "./book-cover";

export type ReadingBook = { title: string; author: string; chapters: { title: string; paragraphs: string[] }[] };

function ChapterPages({ book, chapter, base }: { book: ReadingBook; chapter: number; base: string }) {
  const viewport = useRef<HTMLDivElement>(null);
  const columns = useRef<HTMLDivElement>(null);
  const [layout, setLayout] = useState({ width: 0, count: 1 });
  const [position, setPosition] = useState(0);
  const [large, setLarge] = useState(false);
  const current = book.chapters[chapter];
  useEffect(() => {
    const box = viewport.current!;
    const body = columns.current!;
    let active = true;
    function measure() {
      if (!active) return;
      const width = box.clientWidth;
      body.style.columnWidth = `${width}px`;
      setLayout({ width, count: Math.max(1, Math.round((body.scrollWidth + 32) / (width + 32))) });
    }
    const observer = new ResizeObserver(measure);
    observer.observe(box);
    measure();
    void document.fonts.ready.then(measure);
    return () => { active = false; observer.disconnect(); };
  }, [large]);
  const page = Math.min(position, layout.count - 1);
  return <>
    <div className="reading-tools"><Link href={`${base}?view=contents`}>‹ 目录</Link><span>{book.author}的回忆录</span><button type="button" aria-pressed={large} onClick={() => setLarge(value => !value)}>大字</button></div>
    <article className={`printed-chapter ${large ? "large-type" : ""}`} aria-label={current.title}>
      <header><p>第 {chapter + 1} 章</p><h1>{current.title}</h1></header>
      <div className="page-window" ref={viewport}><div ref={columns} className="printed-columns" style={{ transform: `translateX(-${page * (layout.width + 32)}px)` }}>{current.paragraphs.map((paragraph, index) => <p key={index}>{paragraph}</p>)}</div></div>
      <p className="printed-folio" aria-live="polite">{page + 1} / {layout.count}</p>
    </article>
    <nav className="reading-controls" aria-label="书页翻阅">
      {page > 0 ? <button onClick={() => setPosition(page - 1)}>上一页</button> : chapter > 0 ? <Link href={`${base}?view=read&chapter=${chapter - 1}`}>上一章</Link> : <Link href={base}>书封</Link>}
      <Link href={`${base}?view=contents`}>目录</Link>
      {page + 1 < layout.count ? <button onClick={() => setPosition(page + 1)}>下一页</button> : chapter + 1 < book.chapters.length ? <Link href={`${base}?view=read&chapter=${chapter + 1}`}>下一章</Link> : <Link href={base}>合上书</Link>}
    </nav>
  </>;
}

export function DigitalBook({ book, base, back, view, chapter = 0 }: { book: ReadingBook; base: string; back: string; view?: string; chapter?: number }) {
  const selected = Number.isInteger(chapter) && chapter >= 0 && chapter < book.chapters.length ? chapter : 0;
  if (view === "read" && book.chapters.length) return <main className="digital-book reading-mode"><ChapterPages key={`${base}:${selected}`} book={book} chapter={selected} base={base} /></main>;
  if (view === "contents") return <main className="digital-book"><nav className="reading-tools"><Link href={base}>‹ 书封</Link><span>{book.title}</span></nav><section className="printed-contents"><h1>目 录</h1><ol>{book.chapters.map((item, index) => <li key={index}><Link href={`${base}?view=read&chapter=${index}`}><span>{String(index + 1).padStart(2, "0")}</span><strong>{item.title}</strong></Link></li>)}</ol>{book.chapters.length === 0 ? <p>第一章，等您来讲。</p> : null}</section></main>;
  const count = book.chapters.reduce((sum, item) => sum + item.paragraphs.reduce((n, p) => n + Array.from(p).length, 0), 0);
  return <main className="digital-book"><nav className="reading-tools"><Link href={back}>‹ 返回</Link><span>口述回忆录</span></nav><section className="digital-cover"><BookCover title={book.title} name={book.author} /><p>{book.chapters.length} 章 · {count.toLocaleString("zh-CN")} 字</p><blockquote>{book.chapters[0]?.paragraphs[0].split("。")[0]}{book.chapters.length ? "。" : "第一章，等您来讲。"}</blockquote><Link className="text-link" href={`${base}?view=contents`}>查看目录</Link>{book.chapters.length ? <Link className="primary" href={`${base}?view=read&chapter=0`}>翻开阅读</Link> : null}</section></main>;
}
