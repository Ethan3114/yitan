type EventName = "guide_viewed" | "registration_viewed" | "registration_submitted" | "recording_consent" | "microphone_allowed" | "microphone_denied";
export type PageId = "brand" | "guide_memory" | "guide_voice" | "guide_book" | "guide_family" | "guide_role" | "sample_book" | "login" | "register" | "recover" | "books" | "profile_create" | "profile" | "call" | "chapter" | "memoir" | "family" | "account" | "photos" | "reviews" | "editions" | "other";
type PageEvent = { id: string; name: "page_view" | "page_heartbeat" | "page_leave"; session_id: string; page_id: PageId; page_visit_id: string; duration_ms: number; version: 1 };
let queue = Promise.resolve();
let pending = 0;
const SESSION_KEY = "yitan.analytics.session.v2";
const SESSION_TIMEOUT = 30 * 60 * 1000;

function failed() {
  // 统计失败仅保存次数，避免记录地址参数、用户文字和登录资料。
  const failures = Number(sessionStorage.getItem("yitan.analyticsFailures") || "0");
  sessionStorage.setItem("yitan.analyticsFailures", String(failures + 1));
}

function enqueue(payload: string) {
  pending += 1;
  queue = queue.then(async () => {
    const response = await fetch("/api/v1/events", { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body: payload, keepalive: true });
    if (!response.ok) throw new Error("analytics_unavailable");
  }).catch(failed).finally(() => { pending -= 1; });
}

export function track(name: EventName, step?: number) {
  const payload = JSON.stringify({ id: crypto.randomUUID(), name, session_id: analyticsSession(true), ...(step === undefined ? {} : { step }), version: 1 });
  enqueue(payload);
}

export function analyticsSession(touch = false): string {
  const now = Date.now();
  const stored = sessionStorage.getItem(SESSION_KEY);
  const previous = stored ? JSON.parse(stored) as { id: string; lastActive: number } : null;
  const session = previous && typeof previous.id === "string" && typeof previous.lastActive === "number" && now - previous.lastActive < SESSION_TIMEOUT
    ? previous : { id: crypto.randomUUID(), lastActive: now };
  if (touch) session.lastActive = now;
  sessionStorage.setItem(SESSION_KEY, JSON.stringify(session));
  return session.id;
}

export function pageEvent(name: PageEvent["name"], pageId: PageId, visitId: string, sessionId: string, duration: number, closing = false) {
  const payload: PageEvent = { id: crypto.randomUUID(), name, session_id: sessionId, page_id: pageId, page_visit_id: visitId, duration_ms: Math.min(86400000, Math.max(0, Math.round(duration))), version: 1 };
  if (closing && pending === 0 && navigator.sendBeacon("/api/v1/events", new Blob([JSON.stringify(payload)], { type: "application/json" }))) return;
  enqueue(JSON.stringify(payload));
}
