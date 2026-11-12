import { chromium } from "../frontend/node_modules/@playwright/test/index.mjs";
import { createHash, randomUUID } from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const work = path.join(root, ".intermediate/p0-browser");
const runId = randomUUID();
const runDirectory = path.join(work, runId);
const deviceProbe = process.argv.includes("--device-probe");
const result = {
  run_id: runId,
  started_at: new Date().toISOString(),
  passed: false,
  mode: deviceProbe ? "native_audio_dependency_probe" : "call_panel",
  stage: "prepare",
  browser: { channel: "chrome", headless: false, viewport: { width: 390, height: 844 }, physical_microphone_required: true, media_api_replaced: false, websocket_replaced: false, autoplay_policy_overridden: false },
  received: { ready: 0, opening_requested: 0, audio: 0, interrupted: 0, done: 0, error: 0, other: 0 },
  sent: { audio: 0, before_ready: 0, stop: 0, capture_complete: false },
  timeline: { counts: {}, events: [] },
  native_audio: [],
  page_errors: [],
  checks: [],
};
const sentMixed = [];
const scheduleIds = new Set();
const endedIds = new Set();
let mixedSamples = 0;
let mixedSquare = 0;
let mixedPeak = 0;
let playbackSquare = 0;
let playbackPeak = 0;
let readyAt = 0;
let firstAudioAt = 0;
let firstScheduleAt = 0;
let receivedAudioBytes = 0;
let wireEvent = 0;
let expectedSequence = 0;
let failureCode = "";
let browser;
let context;
let page;

class CheckFailure extends Error {
  constructor(code) { super(code); this.code = code; }
}

function ensure(condition, code) {
  if (!condition) throw new CheckFailure(code);
}

async function privateJson(filename, value) {
  await fs.writeFile(filename, JSON.stringify(value, null, 2) + "\n", { mode: 0o600 });
  await fs.chmod(filename, 0o600);
}

async function waitFor(predicate, timeout, code) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    ensure(!failureCode, failureCode);
    if (await predicate()) return;
    if (page && result.stage !== "login" && result.stage !== "create_profile") {
      const alerts = await page.locator('.notice.error[role="alert"]').allTextContents();
      if (alerts.length) {
        const content = alerts.join(" ");
        const fixed = ["请由老人本人勾选同意录音后再开始。", "语音服务尚未准备好，请联系管理家属。", "麦克风需要安全连接，请通过忆谈的 HTTPS 地址打开。", "请在浏览器设置中允许使用麦克风，再开始访谈。", "没有找到麦克风，请检查设备连接。", "收到的内容格式有误，请重新读取。", "暂时连接不上，请检查网络后重试。已经保存的内容还在。"];
        result.ui_error = fixed.find(message => content.includes(message)) || "unclassified_product_error";
        if (content.includes("没有找到麦克风")) throw new CheckFailure("physical_microphone_not_found");
        if (content.includes("浏览器设置中允许使用麦克风")) throw new CheckFailure("physical_microphone_permission_denied");
        throw new CheckFailure("call_interface_error");
      }
    }
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw new CheckFailure(code);
}

function frame(payload, direction) {
  try {
    const message = JSON.parse(typeof payload === "string" ? payload : payload.toString("utf8"));
    ensure(typeof message.type === "string", "websocket_message_invalid");
    const order = wireEvent++;
    if (direction === "received") {
      const kind = Object.hasOwn(result.received, message.type) ? message.type : "other";
      result.received[kind]++;
      if (message.type === "ready" && !readyAt) { readyAt = Date.now(); result.ready_event = order; }
      if (message.type === "audio") {
        ensure(typeof message.pcm === "string", "provider_audio_missing");
        const bytes = Buffer.from(message.pcm, "base64");
        ensure(bytes.length > 0 && bytes.length % 2 === 0, "provider_pcm_invalid");
        receivedAudioBytes += bytes.length;
        if (!firstAudioAt) { firstAudioAt = Date.now(); result.first_audio_event = order; }
      }
      if (message.type === "error") failureCode = "provider_or_capture_error";
      return;
    }
    if (message.type === "audio") {
      result.sent.audio++;
      if (!readyAt) result.sent.before_ready++;
      ensure(message.seq === expectedSequence++, "capture_sequence_invalid");
      ensure(typeof message.pcm === "string" && typeof message.mixed === "string", "capture_audio_missing");
      const input = Buffer.from(message.pcm, "base64");
      const mixed = Buffer.from(message.mixed, "base64");
      ensure(input.length > 0 && input.length === mixed.length && mixed.length % 2 === 0, "capture_pcm_invalid");
      ensure(mixedSamples + mixed.length / 2 <= 16000 * 120, "capture_duration_unexpected");
      for (let index = 0; index < mixed.length; index += 2) {
        const sample = mixed.readInt16LE(index) / 32768;
        const playback = 2 * sample - input.readInt16LE(index) / 32768;
        mixedSquare += sample * sample;
        playbackSquare += playback * playback;
        mixedPeak = Math.max(mixedPeak, Math.abs(sample));
        playbackPeak = Math.max(playbackPeak, Math.abs(playback));
      }
      mixedSamples += mixed.length / 2;
      sentMixed.push(mixed);
      return;
    }
    if (message.type === "timeline") {
      const allowed = new Set(["start", "schedule", "ended", "interrupt", "suspend", "resume", "stop", "disconnect"]);
      ensure(allowed.has(message.kind), "timeline_kind_invalid");
      ensure(Number.isFinite(message.at_ms) && Number.isFinite(message.wall_ms), "timeline_clock_invalid");
      result.timeline.counts[message.kind] = (result.timeline.counts[message.kind] || 0) + 1;
      const metadata = { kind: message.kind, at_ms: message.at_ms, wall_ms: message.wall_ms, event: order };
      if (message.kind === "schedule" || message.kind === "ended") {
        ensure(Number.isInteger(message.audio_id) && message.audio_id >= 0, "timeline_audio_id_invalid");
        metadata.audio_id = message.audio_id;
        if (message.kind === "schedule") {
          ensure(Number.isFinite(message.duration_ms) && message.duration_ms > 0, "timeline_duration_invalid");
          ensure(firstAudioAt > 0, "playback_scheduled_before_provider_pcm");
          scheduleIds.add(message.audio_id);
          metadata.duration_ms = message.duration_ms;
          if (!firstScheduleAt) { firstScheduleAt = Date.now(); result.first_schedule_event = order; }
        } else {
          ensure(scheduleIds.has(message.audio_id), "playback_ended_without_schedule");
          endedIds.add(message.audio_id);
        }
      }
      result.timeline.events.push(metadata);
    }
    if (message.type === "stop") {
      result.sent.stop++;
      result.sent.capture_complete = message.capture_complete === true;
    }
  } catch (error) {
    failureCode = error instanceof CheckFailure ? error.code : "websocket_payload_invalid";
  }
}

try {
  await fs.mkdir(runDirectory, { recursive: true, mode: 0o700 });
  await fs.chmod(work, 0o700);
  const rawOrigin = process.env.CHECK_ORIGIN || process.argv[2];
  ensure(Boolean(rawOrigin), "public_origin_required");
  const parsed = new URL(rawOrigin);
  ensure(parsed.protocol === "https:" && parsed.pathname === "/" && !parsed.search && !parsed.hash && !parsed.username && !parsed.password, "public_origin_invalid");
  const origin = parsed.origin;
  result.origin = origin;
  const accountFile = path.join(root, ".intermediate/public-runtime/account.json");
  ensure(((await fs.stat(accountFile)).mode & 0o077) === 0, "qa_credentials_permissions_invalid");
  const account = JSON.parse(await fs.readFile(accountFile, "utf8"));
  ensure(typeof account.username === "string" && account.username.startsWith("release-") && typeof account.password === "string" && typeof account.owner_id === "string", "qa_account_invalid");
  const temporary = path.join(root, ".intermediate/tmp");
  await fs.mkdir(temporary, { recursive: true, mode: 0o700 });
  process.env.TMPDIR = temporary;
  result.stage = "browser_launch";
  browser = await chromium.launch({ channel: "chrome", headless: false, ignoreDefaultArgs: ["--mute-audio"] });
  result.stage = "browser_command_line";
  const launchAudit = await browser.newContext();
  const launchPage = await launchAudit.newPage();
  await launchPage.goto("chrome://version");
  const command = await launchPage.locator("#command_line").innerText();
  ensure(!/--[^ ]*(?:fake[^ ]*(?:media|device)|autoplay-policy|mute-audio)/i.test(command), "browser_media_override_detected");
  result.browser.launch_flags_verified = true;
  await launchAudit.close();
  result.stage = "browser_context";
  context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, permissions: ["microphone"] });
  page = await context.newPage();
  page.setDefaultTimeout(30000);
  page.on("response", response => {
    const route = new URL(response.url()).pathname;
    if (route === "/pcm-worklet.js") result.worklet_response = { status: response.status(), content_type: response.headers()["content-type"] || "" };
    if (route === "/api/v1/calls" && response.request().method() === "POST") void response.json().then(body => {
      const code = body.error?.code;
      result.call_open_http = { status: response.status(), error_code: typeof code === "string" && /^[a-z_]{1,64}$/.test(code) ? code : "" };
      if (response.ok() && typeof body.id === "string" && /^[a-f0-9-]{36}$/.test(body.id)) result.call_id = body.id;
    }).catch(() => { failureCode = "call_open_response_invalid"; });
  });
  page.on("requestfailed", request => {
    if (new URL(request.url()).pathname !== "/pcm-worklet.js") return;
    const code = request.failure()?.errorText?.match(/net::(ERR_[A-Z_]+)/)?.[1];
    result.worklet_request_failure = code || "network_request_failed";
  });
  page.on("pageerror", () => { result.page_errors.push("runtime_error"); failureCode = "browser_page_error"; });
  page.on("websocket", socket => {
    const route = new URL(socket.url()).pathname.match(/^\/api\/v1\/calls\/([a-f0-9-]{36})\/live$/);
    if (!route) return;
    if (result.call_id && result.call_id !== route[1]) { failureCode = "multiple_calls_created"; return; }
    result.call_id = route[1];
    socket.on("framereceived", event => frame(event.payload, "received"));
    socket.on("framesent", event => frame(event.payload, "sent"));
    socket.on("socketerror", () => { failureCode = "websocket_transport_error"; });
  });
  const audioInspector = await context.newCDPSession(page);
  result.stage = "native_audio_inspector";
  await audioInspector.send("WebAudio.enable");
  for (const event of ["WebAudio.contextCreated", "WebAudio.contextChanged"]) audioInspector.on(event, ({ context: audio }) => {
    if (audio.contextType === "realtime") result.native_audio.push({ state: audio.contextState, sample_rate: audio.sampleRate, at_ms: Date.now() });
  });

  if (deviceProbe) {
    result.stage = "device_probe";
    await page.goto(`${origin}/login`);
    await page.bringToFront();
    await page.evaluate(() => {
      const button = document.createElement("button");
      button.id = "p0-native-audio-probe";
      button.textContent = "检查本机音频依赖";
      document.body.append(button);
      window.p0NativeAudioProbe = new Promise(resolve => button.addEventListener("click", async () => {
        const observed = { steps: [], granted: false, error_name: "", tracks_stopped: false, context_closed: false };
        let audio, stream;
        try {
          observed.current_step = "audio_context";
          audio = new AudioContext({ sampleRate: 16000 });
          await audio.resume();
          observed.steps.push({ step: "audio_context", state: audio.state, sample_rate: audio.sampleRate });
          observed.current_step = "microphone";
          stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 } });
          observed.granted = stream.getAudioTracks().some(track => track.readyState === "live");
          observed.steps.push({ step: "microphone", active: observed.granted });
          observed.current_step = "worklet_module";
          await audio.audioWorklet.addModule("/pcm-worklet.js");
          observed.steps.push({ step: "worklet_module", loaded: true });
          observed.current_step = "worklet_node";
          const node = new AudioWorkletNode(audio, "pcm-capture", { numberOfInputs: 2 });
          node.disconnect();
          observed.steps.push({ step: "worklet_node", created: true });
        } catch (error) {
          const names = new Set(["NotReadableError", "NotAllowedError", "NotFoundError", "AbortError", "OverconstrainedError", "SecurityError", "NotSupportedError", "TypeError", "ReferenceError"]);
          observed.error_name = names.has(error.name) ? error.name : "UnknownError";
        } finally {
          stream?.getTracks().forEach(track => track.stop());
          observed.tracks_stopped = !stream || stream.getTracks().every(track => track.readyState === "ended");
          if (audio) await audio.close();
          observed.context_closed = !audio || audio.state === "closed";
          resolve(observed);
        }
      }, { once: true }));
    });
    await page.locator("#p0-native-audio-probe").click();
    result.device_probe = await page.evaluate(() => window.p0NativeAudioProbe);
    const module = await context.request.get(`${origin}/pcm-worklet.js`);
    result.worklet_response = { status: module.status(), content_type: module.headers()["content-type"] || "" };
    const actualScript = await module.body();
    const originalScript = await fs.readFile(path.join(root, "frontend/public/pcm-worklet.js"));
    result.worklet_response.sha256 = createHash("sha256").update(actualScript).digest("hex");
    result.worklet_response.source_matches = actualScript.equals(originalScript);
    ensure(module.ok() && /javascript/.test(result.worklet_response.content_type), "worklet_script_unavailable");
    ensure(result.worklet_response.source_matches, "worklet_script_source_mismatch");
    ensure(result.device_probe.granted && result.device_probe.tracks_stopped && result.device_probe.context_closed && result.device_probe.steps.some(step => step.step === "worklet_node"), "native_audio_dependency_failed");
    result.checks = ["真实Chrome媒体启动参数", "原生AudioContext与物理麦克风", "公网worklet与源码哈希", "真实worklet节点创建", "停止tracks与关闭Context"];
    result.passed = true;
    result.stage = "finished";
  } else {
  result.stage = "login";
  await page.goto(`${origin}/login?for=self`);
  await page.getByLabel("账号", { exact: true }).fill(account.username);
  await page.getByLabel("密码", { exact: true }).fill(account.password);
  await page.getByRole("button", { name: "翻开我的回忆录", exact: true }).click();
  await page.waitForURL(url => url.pathname === "/profiles" && url.searchParams.get("new") === "1");
  const meResponse = await context.request.get(`${origin}/api/v1/me`);
  ensure(meResponse.ok(), "qa_session_missing");
  const me = await meResponse.json();
  ensure(me.owner_id === account.owner_id && me.voice_ready === true, "qa_owner_or_voice_invalid");
  const identityResponse = await context.request.get(`${origin}/api/v1/account`);
  ensure(identityResponse.ok() && (await identityResponse.json()).role === "member", "qa_account_must_be_member");

  const reused = process.env.CHECK_PROFILE_ID;
  if (reused) {
    result.stage = "verify_existing_profile";
    ensure(/^[a-f0-9-]{36}$/.test(reused), "verification_profile_id_invalid");
    const response = await context.request.get(`${origin}/api/v1/profiles/${reused}`);
    ensure(response.ok(), "verification_profile_not_owned");
    const existing = await response.json();
    ensure(existing.id === reused && existing.elder_name.startsWith("P0浏览器开场验证-"), "verification_profile_scope_invalid");
    result.profile_id = reused;
  } else {
  result.stage = "create_profile";
  await page.getByLabel("怎么称呼您", { exact: true }).fill(`P0浏览器开场验证-${runId.slice(0, 8)}`);
  await page.getByRole("button", { name: "开始这本回忆录", exact: true }).click();
  await page.waitForURL(url => /^\/profiles\/[a-f0-9-]{36}$/.test(url.pathname));
  result.profile_id = new URL(page.url()).pathname.split("/")[2];
  }
  await privateJson(path.join(runDirectory, "result.json"), result);
  await page.goto(`${origin}/profiles/${result.profile_id}/call`);
  await page.getByRole("button", { name: "开始聊聊", exact: true }).waitFor();
  await page.bringToFront();
  const nativeInterfaces = await page.evaluate(() => ({
    microphone: Function.prototype.toString.call(navigator.mediaDevices.getUserMedia).includes("[native code]"),
    audio_context: Function.prototype.toString.call(AudioContext).includes("[native code]"),
    websocket: Function.prototype.toString.call(WebSocket).includes("[native code]"),
  }));
  ensure(Object.values(nativeInterfaces).every(Boolean), "native_media_interfaces_replaced");
  result.browser.native_interfaces = nativeInterfaces;
  const inputs = await page.evaluate(async () => (await navigator.mediaDevices.enumerateDevices()).filter(device => device.kind === "audioinput").length);
  result.browser.physical_audio_inputs = inputs;
  ensure(inputs > 0, "physical_microphone_not_found");
  await page.evaluate(() => {
    window.p0OpeningPhases = [];
    const phases = new Map([["已接通，正在准备开场，请稍候。", "opening"], ["慢慢讲，随时可以打断我。", "live"], ["正在保存这次访谈，请稍候。", "saving"], ["正在接通，请稍候。", "connecting"]]);
    function observe() {
      const phase = phases.get(document.querySelector(".call-heading p")?.textContent);
      if (phase && window.p0OpeningPhases.at(-1)?.phase !== phase) window.p0OpeningPhases.push({ phase, at_ms: performance.now() });
    }
    new MutationObserver(observe).observe(document.querySelector(".call-heading"), { subtree: true, childList: true, characterData: true });
    observe();
  });
  await page.getByRole("checkbox", { name: "我本人同意录音，用来整理回忆录。随时可以结束访谈。", exact: true }).click();
  result.checkbox_checked = await page.getByRole("checkbox", { name: "我本人同意录音，用来整理回忆录。随时可以结束访谈。", exact: true }).isChecked();
  ensure(result.checkbox_checked, "recording_consent_not_checked");
  result.native_exceptions = [];
  await audioInspector.send("Debugger.enable");
  audioInspector.on("Debugger.paused", event => {
    const names = ["NotReadableError", "NotAllowedError", "NotFoundError", "AbortError", "OverconstrainedError", "SecurityError", "NotSupportedError", "TypeError", "ReferenceError", "SyntaxError", "Error"];
    const description = typeof event.data?.description === "string" ? event.data.description : "";
    const name = names.find(value => new RegExp(`(?:^|\\n)${value}[:\\s]`).test(description));
    const className = typeof event.data?.className === "string" && ["DOMException", "Error", "TypeError", "ReferenceError", "SyntaxError"].includes(event.data.className) ? event.data.className : "other";
    result.native_exceptions.push({ class_name: className, name: name || "unclassified", stage: result.stage });
    void audioInspector.send("Debugger.resume").catch(() => { failureCode = "native_debugger_resume_failed"; });
  });
  await audioInspector.send("Debugger.setPauseOnExceptions", { state: "all" });
  result.stage = "opening";
  await page.getByRole("button", { name: "开始聊聊", exact: true }).click();
  await waitFor(() => readyAt > 0, 30000, "ready_not_received");
  await audioInspector.send("Debugger.setPauseOnExceptions", { state: "none" });
  await waitFor(() => firstAudioAt > 0 && firstScheduleAt > 0 && endedIds.size > 0, 20000, "opening_audio_not_played");
  const phases = await page.evaluate(() => window.p0OpeningPhases);
  ensure(phases.findIndex(item => item.phase === "opening") >= 0 && phases.findIndex(item => item.phase === "live") > phases.findIndex(item => item.phase === "opening"), "opening_live_interface_order_invalid");
  ensure(result.sent.before_ready === 0 && result.received.opening_requested > 0, "opening_protocol_order_invalid");
  ensure(result.sent.audio > 0, "physical_microphone_capture_missing");
  result.browser.physical_microphone_verified = true;
  ensure(result.native_audio.some(audio => audio.state === "running"), "native_audio_context_not_running");
  ensure(await page.getByRole("button", { name: "恢复声音", exact: true }).count() === 0, "audio_context_requires_recovery");
  result.stage = "recording";
  await waitFor(() => Date.now() - readyAt >= 25000, 30000, "recording_duration_invalid");
  ensure(Math.sqrt(playbackSquare / Math.max(1, mixedSamples)) > 0.001, "rendered_playback_not_in_mixed_capture");
  result.stage = "stop";
  await page.getByRole("button", { name: "结束这次访谈", exact: true }).click();
  await waitFor(() => result.received.done > 0, 40000, "done_not_received");
  ensure(result.sent.stop === 1 && result.sent.capture_complete === true, "capture_not_completely_flushed");
  result.phases = await page.evaluate(() => window.p0OpeningPhases);
  ensure(Array.isArray(result.phases) && result.phases.some(item => item.phase === "saving"), "saving_interface_not_observed");

  result.stage = "saved_audio";
  const detailResponse = await context.request.get(`${origin}/api/v1/calls/${result.call_id}`);
  ensure(detailResponse.ok(), "saved_call_missing");
  const detail = await detailResponse.json();
  ensure(detail.id === result.call_id && detail.status === "ready" && !detail.failure_code, "saved_call_not_ready");
  result.saved_call_status = detail.status;
  const recording = await context.request.get(`${origin}/api/v1/calls/${result.call_id}/audio/conversation`);
  ensure(recording.ok() && recording.headers()["content-type"]?.startsWith("audio/wav"), "saved_conversation_missing");
  const audio = await recording.body();
  ensure(audio.length > 44, "saved_conversation_empty");
  const decoded = await page.evaluate(async encoded => {
    const bytes = Uint8Array.from(atob(encoded), character => character.charCodeAt(0));
    const recording = await new OfflineAudioContext(1, 1, 16000).decodeAudioData(bytes.buffer);
    const samples = recording.getChannelData(0);
    let square = 0, peak = 0;
    for (const sample of samples) { square += sample * sample; peak = Math.max(peak, Math.abs(sample)); }
    return { sample_rate: recording.sampleRate, channels: recording.numberOfChannels, samples: samples.length, duration_seconds: recording.duration, rms: Math.sqrt(square / samples.length), peak };
  }, audio.toString("base64"));
  ensure(decoded.sample_rate === 16000 && decoded.channels === 1 && decoded.duration_seconds >= 20, "saved_conversation_duration_invalid");
  ensure(decoded.samples >= mixedSamples && decoded.samples - mixedSamples < 16000, "saved_conversation_sample_count_invalid");
  ensure(decoded.rms > 0.0001 && decoded.peak > 0.002, "saved_conversation_is_silent");
  const captured = Buffer.concat(sentMixed);
  await fs.writeFile(path.join(runDirectory, "captured-mixed.pcm"), captured, { mode: 0o600 });
  await fs.writeFile(path.join(runDirectory, "conversation.wav"), audio, { mode: 0o600 });
  result.captured = { samples: mixedSamples, rms: Math.sqrt(mixedSquare / mixedSamples), peak: mixedPeak, playback_component_rms: Math.sqrt(playbackSquare / mixedSamples), playback_component_peak: playbackPeak, sha256: createHash("sha256").update(captured).digest("hex") };
  result.saved_audio = { ...decoded, sha256: createHash("sha256").update(audio).digest("hex") };
  result.provider_audio_bytes = receivedAudioBytes;
  result.first_audio_after_ready_ms = firstAudioAt - readyAt;
  result.schedule_count = scheduleIds.size;
  result.ended_count = endedIds.size;
  ensure(!failureCode && result.received.error === 0 && result.page_errors.length === 0, failureCode || "unexpected_runtime_error");
  result.checks = ["真实账号与独立验证档案", "物理麦克风与原生音频接口", "ready前未发送录音", "opening先于live", "真实供应商PCM与schedule/ended", "播放声音进入实际混合录音", "真实停止与完整flush", "done与服务端ready", "已保存conversation非静音"];
  result.passed = true;
  result.stage = "finished";
  }
} catch (error) {
  result.failure_code = error instanceof CheckFailure ? error.code : "browser_verification_failed";
  if (!(error instanceof CheckFailure)) {
    const known = [["EACCES", "filesystem_permission_denied"], ["EPERM", "operation_permission_denied"], ["ENOENT", "required_file_missing"]];
    result.failure_category = known.find(([code]) => error?.code === code)?.[1] || "runtime_operation_failed";
  }
  process.exitCode = 1;
} finally {
  if (browser) {
    if (result.call_id && !result.received.done) {
      try {
        const stop = page.getByRole("button", { name: "结束这次访谈", exact: true });
        if (await stop.count() && await stop.isVisible()) {
          await stop.click();
          const deadline = Date.now() + 25000;
          while (!result.received.done && !result.received.error && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 100));
        }
      } catch { result.cleanup = "browser_close_required"; }
    }
    await browser.close();
  }
  result.finished_at = new Date().toISOString();
  await fs.mkdir(runDirectory, { recursive: true, mode: 0o700 });
  await privateJson(path.join(runDirectory, "result.json"), result);
  await privateJson(path.join(work, "result.json"), result);
}

console.log(JSON.stringify({ passed: result.passed, stage: result.stage, failure_code: result.failure_code || "", checks: result.checks.length, first_audio_after_ready_ms: result.first_audio_after_ready_ms ?? null, evidence: path.relative(root, path.join(runDirectory, "result.json")) }));
