import { chromium } from "../frontend/node_modules/@playwright/test/index.mjs";
import { randomUUID } from "node:crypto";
import { spawn } from "node:child_process";
import fs from "node:fs/promises";
import { createServer } from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";

const { values: options, positionals } = parseArgs({ options: { headless: { type: "boolean", default: false }, "preflight-only": { type: "boolean", default: false } }, allowPositionals: true });
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const runId = randomUUID();
const directory = path.join(root, ".intermediate/resource-frequency", runId);
const result = {
  run_id: runId,
  started_at: new Date().toISOString(),
  passed: false,
  stage: "prepare",
  browser: { channel: "chrome", headless: options.headless, artificial_clock: false, visibility_overridden: false, network_mocked: false },
  mode: options["preflight-only"] ? "blank_visibility_preflight" : "resource_frequency",
  events: [],
  visibility: [],
  admin_requests: [],
  checks: [],
  active_draft_polling: { verified: false, reason: "QA家庭没有queued或running任务；本轮未创建整理任务，活跃整理30秒计时未实测。" },
  analytics_cleanup: "本次随机会话保留在已排除统计的QA家庭中。",
};
const adminRequests = new WeakMap();
const eventIdentity = new WeakMap();
let browser;
let nativeChrome;
let nativeChromeExited = false;
let qa;
let adminContext;
let failureCode = "";
let draftReads = 0;
let releaseForeground;

class CheckFailure extends Error {
  constructor(code) { super(code); this.code = code; }
}
function ensure(condition, code) {
  if (!condition) throw new CheckFailure(code);
}
function progress(stage) {
  result.stage = stage;
  process.stdout.write(JSON.stringify({ stage }) + "\n");
}
async function waitFor(predicate, timeout, code) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    ensure(!failureCode, failureCode);
    if (await predicate()) return;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw new CheckFailure(code);
}
async function waitReal(milliseconds) {
  const until = Date.now() + milliseconds;
  await waitFor(() => Date.now() >= until, milliseconds + 1000, "real_wait_timeout");
}
async function privateJson(filename, value) {
  await fs.writeFile(filename, JSON.stringify(value, null, 2) + "\n", { mode: 0o600 });
  await fs.chmod(filename, 0o600);
}
async function credentials(filename) {
  const location = path.join(root, filename);
  ensure(((await fs.stat(location)).mode & 0o077) === 0, "credentials_permissions_invalid");
  const account = JSON.parse(await fs.readFile(location, "utf8"));
  ensure(typeof account.username === "string" && typeof account.password === "string", "credentials_invalid");
  return account;
}
async function authenticate(context, origin, account, role) {
  const response = await context.request.post(`${origin}/api/v1/auth/password-login`, {
    data: { username: account.username, password: account.password },
    headers: { Origin: origin },
  });
  ensure(response.ok(), `${role}_login_failed`);
  const identity = await context.request.get(`${origin}/api/v1/account`);
  ensure(identity.ok() && (await identity.json()).role === role, `${role}_identity_invalid`);
  if (role === "member") {
    const me = await context.request.get(`${origin}/api/v1/me`);
    ensure(me.ok() && (await me.json()).owner_id === account.owner_id, "qa_owner_invalid");
  }
}
async function launchNativeChrome() {
  const reservation = createServer();
  await new Promise((resolve, reject) => { reservation.once("error", reject); reservation.listen(0, "127.0.0.1", resolve); });
  const address = reservation.address();
  ensure(address && typeof address === "object", "debug_port_unavailable");
  await new Promise((resolve, reject) => reservation.close(error => error ? reject(error) : resolve()));
  const executable = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
  await fs.access(executable);
  const profileDirectory = path.join(directory, "chrome-profile");
  await fs.mkdir(profileDirectory, { mode: 0o700 });
  nativeChrome = spawn(executable, [
    ...(options.headless ? ["--headless=new"] : []),
    `--user-data-dir=${profileDirectory}`,
    `--remote-debugging-port=${address.port}`,
    "--remote-debugging-address=127.0.0.1",
    "--no-first-run",
    "--no-default-browser-check",
    "about:blank",
  ], { stdio: "ignore" });
  nativeChrome.once("error", () => { failureCode = "native_chrome_launch_failed"; });
  nativeChrome.once("exit", () => { nativeChromeExited = true; });
  const endpoint = `http://127.0.0.1:${address.port}`;
  await waitFor(async () => {
    ensure(!nativeChromeExited, "native_chrome_exited");
    try {
      const response = await fetch(`${endpoint}/json/version`, { signal: AbortSignal.timeout(1000) });
      result.browser.debugger_http_status = response.status;
      if (!response.ok) return false;
      const version = await response.json();
      ensure(typeof version.webSocketDebuggerUrl === "string" && new URL(version.webSocketDebuggerUrl).hostname === "127.0.0.1", "native_debugger_endpoint_invalid");
      return true;
    } catch (error) {
      const networkCode = error instanceof Error && typeof error.cause?.code === "string" ? error.cause.code : "";
      result.browser.debugger_network_error = ["ECONNREFUSED", "ECONNRESET", "UND_ERR_CONNECT_TIMEOUT"].includes(networkCode) ? networkCode : error instanceof Error ? error.name : "UnknownError";
      if (["ECONNREFUSED", "ECONNRESET", "UND_ERR_CONNECT_TIMEOUT"].includes(networkCode) || error instanceof Error && error.name === "TimeoutError") return false;
      throw error;
    }
  }, 30000, "native_chrome_debugger_unavailable");
  // noDefaults让默认context保留Chrome实际焦点，标签可见性由浏览器决定。
  return chromium.connectOverCDP(endpoint, { noDefaults: true, isLocal: true, artifactsDir: directory });
}
async function focusPage(page) {
  await page.bringToFront();
  if (options.headless) return;
  await new Promise((resolve, reject) => {
    const activation = spawn("/usr/bin/osascript", ["-l", "JavaScript", "-e", 'ObjC.import("AppKit"); function run(argv) { const app = $.NSRunningApplication.runningApplicationWithProcessIdentifier(Number(argv[0])); if (!app.activateWithOptions(2)) throw Error("activation_failed"); }', String(nativeChrome.pid)], { stdio: "ignore" });
    activation.once("error", () => reject(new CheckFailure("native_chrome_activation_failed")));
    activation.once("exit", code => code === 0 ? resolve() : reject(new CheckFailure("native_chrome_activation_failed")));
  });
  await page.bringToFront();
}
function holdForeground(page) {
  if (options.headless) return async () => {};
  let pending;
  const timer = setInterval(() => {
    if (pending) return;
    pending = page.bringToFront().catch(() => { failureCode = "native_chrome_activation_failed"; }).finally(() => { pending = undefined; });
  }, 500);
  return async () => { clearInterval(timer); await pending; };
}
function observe(context, admin = false) {
  context.on("page", page => page.on("pageerror", () => { failureCode = "browser_page_error"; }));
  context.on("request", request => {
    const route = new URL(request.url());
    if (request.method() === "POST" && (/^\/api\/v1\/calls(?:\/|$)/.test(route.pathname) || /\/draft-tasks$/.test(route.pathname))) {
      failureCode = "unexpected_billable_request";
    }
    if (/^\/api\/v1\/draft-tasks\//.test(route.pathname)) draftReads++;
    if (admin && request.method() === "GET" && ["/api/v1/admin/metrics", "/api/v1/admin/accounts", "/api/v1/admin/invitations"].includes(route.pathname)) {
      const item = { path: route.pathname, refresh: route.searchParams.get("refresh") === "true", at_ms: Date.now(), status: null };
      result.admin_requests.push(item);
      adminRequests.set(request, item);
    }
  });
  context.on("response", response => {
    const item = adminRequests.get(response.request());
    if (!item) return;
    item.status = response.status();
    if (admin) {
      const headers = response.headers();
      item.cache = headers["x-metrics-cache"] || "";
      item.cache_max_age = headers["x-metrics-cache-max-age"] || "";
    }
    if (!response.ok()) failureCode = admin ? "admin_http_failed" : "event_http_failed";
  });
}
async function observeTelemetry(page) {
  const inspector = await page.context().newCDPSession(page);
  const requests = new Map();
  const statuses = new Map();
  const tracked = new Set();
  await inspector.send("Network.enable");
  inspector.on("Network.requestWillBeSent", async event => {
    if (new URL(event.request.url).pathname !== "/api/v1/events" || event.request.method !== "POST") return;
    tracked.add(event.requestId);
    const at = Date.now();
    try {
      // CDP直接读取真实beacon的Blob请求体，仅保留事件元数据。
      const body = event.request.postData ?? (await inspector.send("Network.getRequestPostData", { requestId: event.requestId })).postData;
      const payload = JSON.parse(body);
      if (!["page_view", "page_heartbeat", "page_leave"].includes(payload.name)) return;
      ensure(typeof payload.page_id === "string" && typeof payload.page_visit_id === "string" && typeof payload.session_id === "string", "event_identity_invalid");
      const item = { name: payload.name, page_id: payload.page_id, at_ms: at, status: statuses.get(event.requestId) ?? null };
      eventIdentity.set(item, { visit: payload.page_visit_id, session: payload.session_id });
      requests.set(event.requestId, item);
      result.events.push(item);
      if (item.status !== null && (item.status < 200 || item.status >= 300)) failureCode = "event_http_failed";
    } catch (error) {
      if (page.isClosed()) return;
      result.event_payload_error_name = error instanceof Error ? error.name : "UnknownError";
      failureCode = error instanceof CheckFailure ? error.code : "event_payload_invalid";
    }
  });
  inspector.on("Network.responseReceived", event => {
    if (new URL(event.response.url).pathname !== "/api/v1/events") return;
    statuses.set(event.requestId, event.response.status);
    const item = requests.get(event.requestId);
    if (item) item.status = event.response.status;
    if (event.response.status < 200 || event.response.status >= 300) failureCode = "event_http_failed";
  });
  inspector.on("Network.responseReceivedExtraInfo", event => {
    statuses.set(event.requestId, event.statusCode);
    const item = requests.get(event.requestId);
    if (item) item.status = event.statusCode;
    if (tracked.has(event.requestId) && (event.statusCode < 200 || event.statusCode >= 300)) failureCode = "event_http_failed";
  });
  inspector.on("Network.loadingFinished", event => {
    const item = requests.get(event.requestId);
    if (item) item.finished_at_ms = Date.now();
  });
  inspector.on("Network.loadingFailed", event => {
    if (!tracked.has(event.requestId)) return;
    result.event_network_failure = { canceled: event.canceled === true, blocked_reason: event.blockedReason || "" };
    failureCode = "event_network_failed";
  });
}
async function observeVisibility(page) {
  await page.exposeBinding("__yitanFrequencyVisibility", ({ frame }, observation) => {
    if (frame !== page.mainFrame()) return;
    ensure(["visible", "hidden"].includes(observation.state) && Number.isFinite(observation.at_ms), "native_visibility_observation_invalid");
    result.visibility.push({ state: observation.state, at_ms: observation.at_ms });
  });
  await page.addInitScript(() => {
    if (window.top !== window) return;
    const observe = () => { void window.__yitanFrequencyVisibility({ state: document.visibilityState, at_ms: Date.now() }); };
    document.addEventListener("visibilitychange", observe);
    observe();
  });
}
async function visit(page, origin, route) {
  result.navigation_phase = "loading_document";
  await page.goto(origin + route, { waitUntil: "domcontentloaded", timeout: 120000 });
  result.navigation_phase = "waiting_main_or_confirmation";
  await waitFor(async () => Boolean(await page.locator("main").count()) || Boolean(await page.getByText("确定访问", { exact: true }).count()), 90000, "page_unavailable");
  if (!await page.locator("main").count()) {
    result.navigation_phase = "waiting_confirmation";
    const confirmation = page.getByText("确定访问", { exact: true });
    await confirmation.waitFor({ state: "visible" });
    await waitReal(3500);
    if (!await page.locator("main").count()) {
      await Promise.race([confirmation.click(), page.locator("main").waitFor({ state: "visible" })]);
      await page.waitForLoadState("domcontentloaded");
    }
  }
  result.navigation_phase = "document_ready";
}
function events(name, pageId = "brand") {
  return result.events.filter(event => event.name === name && event.page_id === pageId);
}
function reads(resource) {
  return result.admin_requests.filter(request => request.path === `/api/v1/admin/${resource}`);
}
async function waitAccepted(item, code) {
  await waitFor(() => item.status !== null, 30000, `${code}_response_missing`);
  ensure(item.status >= 200 && item.status < 300, `${code}_http_failed`);
}
async function refreshMetrics(page) {
  const count = reads("metrics").length;
  await page.getByRole("button", { name: "刷新数据", exact: true }).click();
  await waitFor(() => reads("metrics").length === count + 1, 30000, "metrics_refresh_missing");
  const latest = reads("metrics").at(-1);
  await waitAccepted(latest, "metrics_refresh");
  ensure(latest.refresh && latest.cache === "refresh" && latest.cache_max_age === "300", "metrics_refresh_contract_invalid");
}

try {
  await fs.mkdir(directory, { recursive: true, mode: 0o700 });
  await fs.chmod(path.dirname(directory), 0o700);
  ensure(positionals.length <= 1, "origin_arguments_invalid");
  const parsed = new URL(positionals[0] || process.env.CHECK_ORIGIN || "");
  const allowedOrigin = parsed.origin === "https://yitan-web-341879-6-1525683845.sh.run.tcloudbase.com" || ["http:", "https:"].includes(parsed.protocol) && ["localhost", "127.0.0.1"].includes(parsed.hostname);
  ensure(allowedOrigin && parsed.pathname === "/" && !parsed.search && !parsed.hash && !parsed.username && !parsed.password, "origin_invalid");
  const origin = parsed.origin;
  result.origin = origin;
  const temporary = path.join(root, ".intermediate/tmp");
  await fs.mkdir(temporary, { recursive: true, mode: 0o700 });
  process.env.TMPDIR = temporary;
  progress("native_browser_launch");
  browser = await launchNativeChrome();
  qa = browser.contexts()[0];
  ensure(qa, "native_default_context_missing");
  const page = await qa.newPage();
  page.setDefaultTimeout(30000);
  await page.setViewportSize({ width: 390, height: 844 });
  progress("native_visibility_preflight");
  const foreground = await qa.newPage();
  await focusPage(foreground);
  await waitFor(() => page.evaluate(() => document.visibilityState === "hidden"), 10000, "native_hidden_state_unavailable");
  await focusPage(page);
  await waitFor(() => page.evaluate(() => document.visibilityState === "visible"), 10000, "native_visible_state_unavailable");
  result.browser.focus_emulation_disabled = true;
  result.browser.connection = "connectOverCDP_noDefaults";
  result.browser.viewport = { width: 390, height: 844 };
  if (options["preflight-only"]) {
    result.checks.push("两个about:blank标签通过Chrome原生激活产生hidden和visible");
    result.passed = true;
    progress("finished");
  } else {
  const account = await credentials(".intermediate/public-runtime/account.json");
  const administrator = await credentials(".intermediate/private/cloud-admin.json");
  ensure(account.username.startsWith("release-") && typeof account.owner_id === "string", "qa_account_invalid");
  const existingP0 = JSON.parse(await fs.readFile(path.join(root, ".intermediate/p0-browser/6ba0604c-6e31-4e57-8a04-33205e9865a2/result.json"), "utf8"));
  ensure(typeof existingP0.profile_id === "string" && /^[a-f0-9-]{36}$/.test(existingP0.profile_id), "qa_profile_id_invalid");
  progress("login_before_navigation");
  await authenticate(qa, origin, account, "member");
  observe(qa);
  await observeTelemetry(page);
  await observeVisibility(page);
  page.on("pageerror", () => { failureCode = "browser_page_error"; });
  const profileResponse = await qa.request.get(`${origin}/api/v1/profiles/${existingP0.profile_id}`);
  ensure(profileResponse.ok(), "qa_profile_unavailable");
  const profile = await profileResponse.json();
  ensure(profile.id === existingP0.profile_id && profile.elder_name.startsWith("P0浏览器开场验证-"), "qa_profile_scope_invalid");
  ensure(profile.calls.length > 0 && profile.calls.every(call => !["queued", "running"].includes(call.task?.status) && call.status !== "live"), "qa_active_task_or_call_exists");
  result.inactive_draft = { existing_calls: profile.calls.length, active_tasks: 0, observed_ms: 0, status_queries: 0 };
  releaseForeground = holdForeground(page);
  progress("opening_welcome");
  await visit(page, origin, "/welcome?step=0");
  progress("focusing_welcome");
  await page.bringToFront();
  progress("initial_view_acknowledgement");
  await waitFor(() => events("page_view").length === 1, 30000, "initial_view_missing");
  const initial = events("page_view")[0];
  await waitAccepted(initial, "initial_view");
  const initialIdentity = eventIdentity.get(initial);
  progress("visible_60_second_heartbeat");
  await waitFor(async () => {
    ensure(await page.evaluate(() => document.visibilityState === "visible"), "visible_measurement_interrupted");
    return events("page_heartbeat").length > 0;
  }, 75000, "heartbeat_missing");
  const heartbeat = events("page_heartbeat")[0];
  await waitAccepted(heartbeat, "heartbeat");
  result.first_heartbeat_ms = heartbeat.at_ms - initial.at_ms;
  ensure(result.first_heartbeat_ms >= 55000 && result.first_heartbeat_ms <= 75000, "heartbeat_interval_invalid");
  ensure(!result.visibility.some(observation => observation.state === "hidden" && observation.at_ms >= initial.at_ms && observation.at_ms <= heartbeat.at_ms), "visible_measurement_interrupted");
  ensure(eventIdentity.get(heartbeat).visit === initialIdentity.visit && eventIdentity.get(heartbeat).session === initialIdentity.session, "heartbeat_identity_changed");
  result.checks.push("真实可见页面约60秒发送首个心跳，访问与会话保持一致");

  progress("hidden_65_seconds");
  await releaseForeground();
  releaseForeground = undefined;
  await focusPage(foreground);
  releaseForeground = holdForeground(foreground);
  await waitFor(() => page.evaluate(() => document.visibilityState === "hidden"), 10000, "native_hidden_state_unavailable");
  await waitFor(() => events("page_leave").some(event => eventIdentity.get(event).visit === initialIdentity.visit), 30000, "hidden_leave_missing");
  await waitAccepted(events("page_leave").find(event => eventIdentity.get(event).visit === initialIdentity.visit), "hidden_leave");
  const hiddenHeartbeats = result.events.filter(event => event.name === "page_heartbeat").length;
  const hiddenAt = Date.now();
  const hiddenRoute = page.url();
  await waitFor(async () => {
    ensure(await page.evaluate(() => document.visibilityState === "hidden"), "page_became_visible_during_hidden_check");
    ensure(page.url() === hiddenRoute, "hidden_page_route_changed");
    return Date.now() - hiddenAt >= 65000;
  }, 66000, "hidden_measurement_timeout");
  ensure(await page.evaluate(() => document.visibilityState === "hidden"), "page_became_visible_during_hidden_check");
  ensure(!result.visibility.some(observation => observation.state === "visible" && observation.at_ms >= hiddenAt), "hidden_measurement_interrupted");
  ensure(result.events.filter(event => event.name === "page_heartbeat").length === hiddenHeartbeats, "hidden_page_sent_heartbeat");
  result.hidden_observed_ms = Date.now() - hiddenAt;
  result.checks.push("实际切换Chrome标签后连续65秒隐藏，发送离开事件且不发送心跳");

  progress("resume_and_leave");
  await releaseForeground();
  releaseForeground = holdForeground(page);
  await focusPage(page);
  await waitFor(() => page.evaluate(() => document.visibilityState === "visible"), 10000, "native_visible_state_unavailable");
  await waitFor(() => events("page_view").length === 2, 30000, "resumed_view_missing");
  const resumed = events("page_view")[1];
  await waitAccepted(resumed, "resumed_view");
  const resumedIdentity = eventIdentity.get(resumed);
  ensure(resumedIdentity.visit !== initialIdentity.visit && resumedIdentity.session === initialIdentity.session, "resumed_identity_invalid");
  await page.locator(".guide-controls button.primary").click();
  await waitFor(() => events("page_leave").some(event => eventIdentity.get(event).visit === resumedIdentity.visit) && events("page_view", "guide_memory").length > 0, 30000, "navigation_events_missing");
  await waitAccepted(events("page_leave").find(event => eventIdentity.get(event).visit === resumedIdentity.visit), "navigation_leave");
  await waitAccepted(events("page_view", "guide_memory")[0], "guide_memory_view");
  result.checks.push("恢复可见建立新访问并延续同一会话，实际跳转发送离开和进入事件");

  progress("inactive_draft_31_seconds");
  await visit(page, origin, `/profiles/${existingP0.profile_id}`);
  await waitFor(() => events("page_view", "profile").length > 0, 30000, "profile_view_missing");
  await waitAccepted(events("page_view", "profile")[0], "profile_view");
  await page.locator(".task-card").first().waitFor();
  const inactiveAt = Date.now();
  const originalDraftReads = draftReads;
  await waitFor(async () => {
    ensure(await page.evaluate(() => document.visibilityState === "visible"), "inactive_draft_not_visible");
    return Date.now() - inactiveAt >= 31000;
  }, 32000, "inactive_draft_measurement_timeout");
  result.inactive_draft.observed_ms = Date.now() - inactiveAt;
  result.inactive_draft.status_queries = draftReads - originalDraftReads;
  ensure(result.inactive_draft.status_queries === 0, "inactive_draft_polled");
  ensure(await page.evaluate(() => document.visibilityState === "visible"), "inactive_draft_not_visible");
  result.checks.push("QA已有访谈的非活跃整理卡片显示31秒，未查询整理任务状态");
  await releaseForeground();
  releaseForeground = undefined;
  for (const qaPage of qa.pages()) await qaPage.close();
  qa = undefined;

  progress("admin_lazy_accounts");
  adminContext = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  await authenticate(adminContext, origin, administrator, "admin");
  observe(adminContext, true);
  const dashboard = await adminContext.newPage();
  dashboard.setDefaultTimeout(30000);
  await visit(dashboard, origin, "/admin");
  await dashboard.locator('.admin-workspace[data-layout="desktop-admin"]').waitFor();
  await dashboard.getByRole("heading", { name: "体验总览", exact: true }).waitFor();
  await waitFor(() => reads("metrics").length > 0 && reads("metrics").every(request => request.status !== null), 60000, "initial_metrics_missing");
  ensure(reads("metrics").every(request => !request.refresh && request.status === 200), "initial_metrics_invalid");
  ensure(reads("accounts").length === 0 && reads("invitations").length === 0, "overview_loaded_accounts");
  await refreshMetrics(dashboard);
  ensure(reads("accounts").length === 0 && reads("invitations").length === 0, "overview_refresh_loaded_accounts");
  ensure((await dashboard.locator(".admin-data-caption").textContent()).includes("更新于"), "statistics_as_of_missing");
  result.checks.push("后台总览和总览刷新仅读取统计，刷新使用refresh=true与300秒缓存约定");

  await dashboard.getByRole("button", { name: /^账户与邀请/ }).click();
  await dashboard.getByRole("heading", { name: "家庭账户", exact: true }).waitFor();
  await dashboard.getByRole("heading", { name: "邀请码记录", exact: true }).waitFor();
  await waitFor(() => reads("accounts").length === 1 && reads("invitations").length === 1, 30000, "accounts_activation_missing");
  await Promise.all([waitAccepted(reads("accounts")[0], "accounts"), waitAccepted(reads("invitations")[0], "invitations")]);
  result.checks.push("打开账户与邀请后才实际读取账户和邀请码，各读取一次");
  await dashboard.getByRole("button", { name: /^体验总览/ }).click();
  await refreshMetrics(dashboard);
  ensure(reads("accounts").length === 1 && reads("invitations").length === 1, "inactive_accounts_refreshed");
  await dashboard.getByRole("button", { name: /^账户与邀请/ }).click();
  await waitFor(() => reads("accounts").length === 2 && reads("invitations").length === 2, 30000, "accounts_reactivation_missing");
  await Promise.all([waitAccepted(reads("accounts")[1], "accounts_reactivation"), waitAccepted(reads("invitations")[1], "invitations_reactivation")]);
  await refreshMetrics(dashboard);
  await waitFor(() => reads("accounts").length === 3 && reads("invitations").length === 3, 30000, "accounts_manual_refresh_missing");
  await Promise.all([waitAccepted(reads("accounts")[2], "accounts_refresh"), waitAccepted(reads("invitations")[2], "invitations_refresh")]);
  result.checks.push("离开账户页面停止读取，再次打开及手动刷新均实际请求最新账户与邀请码");
  ensure(!failureCode, failureCode);
  result.passed = true;
  progress("finished");
  }
} catch (error) {
  result.failure = error instanceof CheckFailure ? error.code : "runtime_error";
  result.error_name = error instanceof Error ? error.name : "UnknownError";
  result.failure_location = error instanceof Error ? [...(error.stack || "").matchAll(/check_resource_frequency\.mjs:\d+:\d+/g)].map(match => match[0]) : [];
  process.exitCode = 1;
} finally {
  if (releaseForeground) await releaseForeground();
  for (const surface of [qa, adminContext, browser]) {
    if (surface && surface !== browser?.contexts()[0]) await surface.close().catch(() => { result.cleanup_error = true; process.exitCode = 1; });
  }
  if (nativeChrome && !nativeChromeExited) nativeChrome.kill("SIGTERM");
  if (nativeChrome) await Promise.race([new Promise(resolve => { if (nativeChromeExited) resolve(); else nativeChrome.once("exit", resolve); }), new Promise(resolve => setTimeout(resolve, 5000))]);
  result.browser.native_process_closed = !nativeChrome || nativeChromeExited;
  result.finished_at = new Date().toISOString();
  await fs.mkdir(directory, { recursive: true, mode: 0o700 });
  await privateJson(path.join(directory, "result.json"), result);
  process.stdout.write(JSON.stringify({ passed: result.passed, stage: result.stage, checks: result.checks.length, failure: result.failure || "", evidence: path.relative(root, path.join(directory, "result.json")), active_draft_polling_verified: false }) + "\n");
}
