type EventName = "guide_viewed" | "registration_viewed" | "registration_submitted" | "recording_consent" | "microphone_allowed" | "microphone_denied";
let queue = Promise.resolve();

export function track(name: EventName, step?: number) {
  const payload = JSON.stringify({ id: crypto.randomUUID(), name, ...(step === undefined ? {} : { step }), version: 1 });
  queue = queue.then(async () => {
    const response = await fetch("/api/v1/events", { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body: payload, keepalive: true });
    if (!response.ok) throw new Error("analytics_unavailable");
  }).catch(() => {
    // 统计上报失败仅记录次数，不写入用户内容，也不阻断访谈。
    const failures = Number(sessionStorage.getItem("yitan.analyticsFailures") || "0");
    sessionStorage.setItem("yitan.analyticsFailures", String(failures + 1));
  });
}
