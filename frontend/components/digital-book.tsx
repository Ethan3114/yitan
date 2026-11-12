"use client";
import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { BookCover } from "./book-cover";
import { PageBook, type BookLeaf, type PageBookHandle } from "./page-book";

export type ReadingBook = { title: string; author: string; chapters: { title: string; paragraphs: string[] }[] };

function ChapterPages({ book, chapter, base }: { book: ReadingBook; chapter: number; base: string }) {
  const viewport = useRef<HTMLDivElement>(null);
  const columns = useRef<HTMLDivElement>(null);
  const turningBook = useRef<PageBookHandle>(null);
  const [pagination, setPagination] = useState<{ text: string; continued: boolean }[][]>([]);
  const [position, setPosition] = useState(0);
  const [large, setLarge] = useState(false);
  const [busy, setBusy] = useState(true);
  const current = book.chapters[chapter];
  useEffect(() => {
    const box = viewport.current!;
    const body = columns.current!;
    let active = true;
    function measure() {
      if (!active) return;
      const width = box.clientWidth;
      body.style.columnWidth = `${width}px`;
      const left = body.getBoundingClientRect().left;
      const result: { text: string; continued: boolean }[][] = [];
      for (const paragraph of body.querySelectorAll("p")) {
        const text = paragraph.firstChild!;
        const range = document.createRange();
        let offset = 0;
        let previousPage = -1;
        for (const character of Array.from(text.textContent!)) {
          range.setStart(text, offset);
          range.setEnd(text, offset + character.length);
          const index = Math.max(0, Math.floor((range.getBoundingClientRect().left - left + 1) / (width + 32)));
          result[index] ??= [];
          if (index !== previousPage) result[index].push({ text: "", continued: offset > 0 });
          result[index].at(-1)!.text += character;
          previousPage = index;
          offset += character.length;
        }
      }
      setPagination(previous => JSON.stringify(previous) === JSON.stringify(result) ? previous : result);
    }
    const observer = new ResizeObserver(measure);
    observer.observe(box);
    measure();
    void document.fonts.ready.then(measure);
    return () => { active = false; observer.disconnect(); };
  }, [large, current]);
  const count = Math.max(1, pagination.length);
  const page = Math.min(position, count - 1);
  const leaves = useMemo<BookLeaf[]>(() => pagination.map((paragraphs, index) => ({ key: String(index), label: `${current.title}，第 ${index + 1} 页`, content:
    <article className={`printed-chapter ${large ? "large-type" : ""}`} aria-label={current.title}>
      <header><p>第 {chapter + 1} 章{index ? " · 续页" : ""}</p><h1>{current.title}</h1></header>
      <div className="page-window"><div className="printed-page-body">{paragraphs.map((paragraph, paragraphIndex) => <p key={paragraphIndex} className={paragraph.continued ? "continued-paragraph" : undefined}>{paragraph.text}</p>)}</div></div>
      <p className="printed-folio" aria-hidden="true">{index + 1} / {pagination.length}</p>
    </article>
  })), [pagination, chapter, current, large]);
  return <>
    <div className="reading-tools"><Link href={`${base}?view=contents`}>‹ 目录</Link><span>{book.author}的回忆录</span><button type="button" aria-pressed={large} onClick={() => setLarge(value => !value)}>大字</button></div>
    <div className="reading-binding">
    <article className={`printed-chapter measurement-chapter ${large ? "large-type" : ""}`} aria-hidden="true" inert>
      <header><p>第 {chapter + 1} 章</p><h1>{current.title}</h1></header>
      <div className="page-window" ref={viewport}><div ref={columns} className="printed-columns">{current.paragraphs.map((paragraph, index) => <p key={index}>{paragraph}</p>)}</div></div>
      <p className="printed-folio">1 / 1</p>
    </article>
    {leaves.length ? <PageBook ref={turningBook} leaves={leaves} page={page} className="reading-page-book" onPageChange={setPosition} onBusyChange={setBusy} /> : <div className="reading-page-book" role="status">正在排印书页…</div>}
    </div>
    <p className="book-page-announcement" aria-live="polite">第 {chapter + 1} 章，第 {page + 1} 页，共 {count} 页</p>
    <nav className="reading-controls" aria-label="书页翻阅">
      {page > 0 ? <button disabled={busy} onClick={() => turningBook.current?.turnTo(page - 1)}>上一页</button> : chapter > 0 ? <Link href={`${base}?view=read&chapter=${chapter - 1}`}>上一章</Link> : <Link href={base}>书封</Link>}
      <Link href={`${base}?view=contents`}>目录</Link>
      {page + 1 < count ? <button disabled={busy} onClick={() => turningBook.current?.turnTo(page + 1)}>下一页</button> : chapter + 1 < book.chapters.length ? <Link href={`${base}?view=read&chapter=${chapter + 1}`}>下一章</Link> : <Link href={base}>合上书</Link>}
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
