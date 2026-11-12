"use client";

import { useEffect } from "react";
import { usePathname, useSearchParams } from "next/navigation";
import { analyticsSession, pageEvent, type PageId } from "@/lib/analytics";

const GUIDE_PAGES: PageId[] = ["brand", "guide_memory", "guide_voice", "guide_book", "guide_family", "guide_role"];

function identifyPage(path: string, step: string | null, tab: string | null, creating: string | null): PageId {
  if (path === "/") return "brand";
  if (path === "/welcome") return GUIDE_PAGES[Number(step || 0)] || "brand";
  if (path === "/books" || path.startsWith("/books/")) return "sample_book";
  if (path === "/profiles") return creating === "1" ? "profile_create" : "books";
  if (path.startsWith("/profiles/")) {
    if (path.endsWith("/call")) return "call";
    if (path.endsWith("/book")) return "memoir";
    if (path.includes("/chapters/")) return "chapter";
    if (path.endsWith("/family")) return "family";
    if (path.endsWith("/handoff")) return "call";
    if (tab === "photos") return "photos";
    if (tab === "reviews") return "reviews";
    return "profile";
  }
  if (path === "/login" || path === "/register" || path === "/recover" || path === "/account") return path.slice(1) as PageId;
  if (path === "/family" || path === "/join") return "family";
  return "other";
}

export function ExperienceTracker() {
  const path = usePathname();
  const query = useSearchParams();
  const pageId = identifyPage(path, query.get("step"), query.get("tab"), query.get("new"));
  const excluded = path.startsWith("/admin") || (path === "/login" && query.get("next") === "/admin");

  useEffect(() => {
    if (excluded) return;
    let visitId = "";
    let sessionId = "";
    let openedAt = 0;
    let lastInteraction = Date.now();
    let active = false;
    function open() {
      if (active || document.visibilityState !== "visible") return;
      sessionId = analyticsSession(true);
      visitId = crypto.randomUUID();
      openedAt = Date.now();
      lastInteraction = openedAt;
      active = true;
      pageEvent("page_view", pageId, visitId, sessionId, 0);
    }
    function close() {
      if (!active) return;
      pageEvent("page_leave", pageId, visitId, sessionId, Date.now() - openedAt, true);
      active = false;
    }
    function activity() {
      const now = Date.now();
      if (now - lastInteraction < 1000) return;
      if (now - lastInteraction >= 30 * 60 * 1000) { close(); open(); }
      lastInteraction = now;
      analyticsSession(true);
    }
    function visibility() {
      if (document.visibilityState === "visible") open();
      else close();
    }
    open();
    const timer = window.setInterval(() => {
      if (active && document.visibilityState === "visible" && Date.now() - lastInteraction < 30 * 60 * 1000) {
        analyticsSession(true);
        pageEvent("page_heartbeat", pageId, visitId, sessionId, Date.now() - openedAt);
      }
    }, 15000);
    const events = ["pointerdown", "keydown", "scroll"] as const;
    events.forEach(event => window.addEventListener(event, activity, { passive: true }));
    window.addEventListener("yitan:book-activity", activity);
    document.addEventListener("visibilitychange", visibility);
    window.addEventListener("pagehide", close);
    window.addEventListener("pageshow", visibility);
    return () => {
      close();
      window.clearInterval(timer);
      events.forEach(event => window.removeEventListener(event, activity));
      window.removeEventListener("yitan:book-activity", activity);
      document.removeEventListener("visibilitychange", visibility);
      window.removeEventListener("pagehide", close);
      window.removeEventListener("pageshow", visibility);
    };
  }, [pageId, excluded]);
  return null;
}
