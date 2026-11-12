"use client";

import { useEffect, useEffectEvent, useImperativeHandle, useRef, type ReactNode, type Ref } from "react";
import type { PageFlip } from "page-flip";

export type BookLeaf = { key: string; label: string; content: ReactNode; hard?: boolean };
export type PageBookHandle = { turnTo: (index: number) => void };

export function PageBook({ leaves, page, onPageChange, onBusyChange, className = "", ref }: {
  leaves: BookLeaf[];
  page: number;
  onPageChange: (page: number) => void;
  onBusyChange: (busy: boolean) => void;
  className?: string;
  ref?: Ref<PageBookHandle>;
}) {
  const stage = useRef<HTMLDivElement>(null);
  const source = useRef<HTMLDivElement>(null);
  const engine = useRef<PageFlip | null>(null);
  const requestedPage = useRef(page);
  const notifyPage = useEffectEvent(onPageChange);
  const notifyBusy = useEffectEvent(onBusyChange);

  useImperativeHandle(ref, () => ({ turnTo(index: number) {
    const book = engine.current;
    if (!book || stage.current?.dataset.engine !== "page-flip" || book.getState() !== "read" || index < 0 || index >= leaves.length) return;
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) book.turnToPage(index);
    else book.flip(index, "bottom");
  } }), [leaves.length]);

  useEffect(() => {
    requestedPage.current = page;
    const book = engine.current;
    if (book && book.getState() === "read" && book.getCurrentPageIndex() !== page) book.turnToPage(page);
  }, [page]);

  useEffect(() => {
    const container = stage.current!;
    const originals = source.current!;
    let disposed = false;
    let dimensions = "";
    let generation = 0;
    let frame: HTMLIFrameElement | undefined;
    notifyBusy(true);

    function release() {
      engine.current?.off("flip");
      engine.current?.off("changeState");
      engine.current?.destroy();
      engine.current = null;
      frame?.remove();
    }

    function mount() {
        const width = container.clientWidth;
        const height = container.clientHeight;
        const nextDimensions = `${width}:${height}`;
        if (!width || !height || dimensions === nextDimensions) return;
        dimensions = nextDimensions;
        const version = ++generation;
        const previous = engine.current?.getCurrentPageIndex() ?? requestedPage.current;
        release();
        notifyBusy(true);
        container.dataset.engine = "loading";
        container.dataset.motion = "loading";
        const sheetFrame = document.createElement("iframe");
        frame = sheetFrame;
        sheetFrame.className = "page-book-frame";
        sheetFrame.title = "回忆录书页";
        sheetFrame.sandbox.add("allow-scripts", "allow-same-origin", "allow-top-navigation-by-user-activation");
        sheetFrame.srcdoc = '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head><body></body></html>';
        sheetFrame.addEventListener("load", () => { void prepare(); }, { once: true });
        container.append(sheetFrame);

        async function prepare() {
        if (disposed || version !== generation) return;
        const paperDocument = sheetFrame.contentDocument!;
        const paperWindow = sheetFrame.contentWindow as Window & { St: { PageFlip: typeof PageFlip } };
        const styles = Array.from(document.querySelectorAll("link[rel=stylesheet],style"), element => {
          const copy = paperDocument.importNode(element, true);
          const ready = copy.nodeName === "LINK" ? new Promise<void>((resolve, reject) => {
            copy.addEventListener("load", () => resolve(), { once: true });
            copy.addEventListener("error", () => reject(new Error("书页样式读取失败")), { once: true });
          }) : Promise.resolve();
          paperDocument.head.append(copy);
          return ready;
        });
        const sizing = paperDocument.createElement("style");
        sizing.textContent = "html,body{margin:0!important;width:100%!important;height:100%!important;min-height:0!important;overflow:hidden!important}body{background:transparent!important}.page-book-engine{width:100%;height:100%}.turning-leaf{height:100%;overflow:hidden}.guide-leaf,.printed-chapter{height:100%!important;min-height:0!important}.page-book-engine a>*{pointer-events:none}";
        paperDocument.head.append(sizing);
        const script = paperDocument.createElement("script");
        script.src = "/book-engine?version=2.0.7";
        const library = new Promise<void>((resolve, reject) => {
          script.addEventListener("load", () => resolve(), { once: true });
          script.addEventListener("error", () => reject(new Error("翻页组件读取失败")), { once: true });
        });
        paperDocument.head.append(script);
        await Promise.all([...styles, library]);
        if (disposed || version !== generation) return;
        const host = document.createElement("div");
        host.className = "page-book-engine";
        const sheets = Array.from(originals.children, element => {
          const copy = paperDocument.importNode(element, true) as HTMLElement;
          const originalElements = [element, ...element.querySelectorAll("*")];
          const copiedElements = [copy, ...copy.querySelectorAll<HTMLElement>("*")];
          // 固定父页面排好的字体与边距，书页内的媒体查询不再改变文字尺寸。
          const properties = ["font-family", "font-size", "font-weight", "font-style", "line-height", "letter-spacing", "word-spacing", "text-indent", "padding-top", "padding-right", "padding-bottom", "padding-left", "gap", "outline-offset"];
          for (const [index, original] of originalElements.entries()) {
            const style = getComputedStyle(original);
            for (const property of properties) copiedElements[index].style.setProperty(property, style.getPropertyValue(property));
            if (original.classList.contains("book-cover")) copiedElements[index].style.width = style.width;
            if (original.classList.contains("book-cover-inner")) copiedElements[index].style.minHeight = style.minHeight;
          }
          for (const anchor of copy.querySelectorAll("a")) anchor.target = "_top";
          return copy;
        });
        host.append(...sheets);
        paperDocument.body.append(host);
        paperDocument.body.tabIndex = 0;
        await paperDocument.fonts.ready;
        if (disposed || version !== generation) return;
        // PageFlip 按翻动距离缩短时长，换算后保持按钮翻页约 750 毫秒。
        const distance = Math.floor(Math.max(width * 2 - height / 10, height / 10)) + 1;
        const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
        const flippingTime = reduced ? 1 : Math.round(750 * 1000 / Math.min(1000, distance));
        const Flip = paperWindow.St.PageFlip;
        const book = new Flip(host, { width, height, size: "fixed", autoSize: false, usePortrait: true, startPage: previous, showCover: false, flippingTime, drawShadow: true, maxShadowOpacity: .3, mobileScrollSupport: true, useMouseEvents: true, clickEventForward: true, disableFlipByClick: false, showPageCorners: !reduced, swipeDistance: 45 });
        engine.current = book;
        let rendered = false;
        function announce() {
          if (!rendered) return;
          const current = book.getCurrentPageIndex();
          container.dataset.pageIndex = String(current);
          container.dataset.engine = "page-flip";
          for (const leaf of host.querySelectorAll<HTMLElement>("[data-leaf-index]")) {
            const hidden = Number(leaf.dataset.leafIndex) !== current;
            leaf.setAttribute("aria-hidden", String(hidden));
            leaf.inert = hidden;
          }
          notifyPage(current);
        }
        book.on("flip", announce);
        book.on("changeState", event => {
          const motion = String(event.data);
          container.dataset.motion = motion;
          container.setAttribute("aria-busy", String(motion !== "read"));
          notifyBusy(motion !== "read");
          if (motion === "read") announce();
        });
        let lastActivity = -Infinity;
        function activity() {
          const now = performance.now();
          if (now - lastActivity < 1000) return;
          lastActivity = now;
          window.dispatchEvent(new Event("yitan:book-activity"));
        }
        paperDocument.addEventListener("pointerdown", activity, { passive: true });
        paperDocument.addEventListener("pointermove", activity, { passive: true });
        paperDocument.addEventListener("scroll", activity, { passive: true, capture: true });
        paperDocument.addEventListener("keydown", event => {
          activity();
          const target = event.target as HTMLElement;
          if (!rendered || target.closest("a,button,input,textarea,select") || !["ArrowLeft", "ArrowRight"].includes(event.key) || book.getState() !== "read") return;
          const index = book.getCurrentPageIndex() + (event.key === "ArrowRight" ? 1 : -1);
          if (index < 0 || index >= leaves.length) return;
          event.preventDefault();
          if (matchMedia("(prefers-reduced-motion: reduce)").matches) book.turnToPage(index);
          else book.flip(index, "bottom");
        });
        book.loadFromHTML(sheets);
        paperWindow.requestAnimationFrame(() => {
          if (disposed || version !== generation) return;
          rendered = true;
          container.dataset.motion = "read";
          container.setAttribute("aria-busy", "false");
          notifyBusy(false);
          announce();
        });
      }
    }
    const observer = new ResizeObserver(mount);
    observer.observe(container);
    mount();

    return () => {
      disposed = true;
      observer.disconnect();
      generation++;
      release();
    };
  }, [leaves]);

  return <>
    <div ref={stage} className={`page-book ${className}`} data-engine="loading" data-motion="loading" tabIndex={0} aria-label="翻阅书页，可拖动页角或使用翻页按钮" onKeyDown={event => {
      if (event.target !== event.currentTarget || !["ArrowLeft", "ArrowRight"].includes(event.key)) return;
      event.preventDefault();
      const index = (engine.current?.getCurrentPageIndex() ?? page) + (event.key === "ArrowRight" ? 1 : -1);
      if (index >= 0 && index < leaves.length && engine.current?.getState() === "read") {
        if (matchMedia("(prefers-reduced-motion: reduce)").matches) engine.current.turnToPage(index);
        else engine.current.flip(index, "bottom");
      }
    }} />
    <div hidden ref={source}>{leaves.map((leaf, index) => <div key={leaf.key} className="turning-leaf" data-leaf-index={index} data-density={leaf.hard ? "hard" : "soft"} role="group" aria-label={leaf.label}>{leaf.content}</div>)}</div>
  </>;
}
