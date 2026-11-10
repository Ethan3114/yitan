import { chromium } from "@playwright/test";
import { execFileSync } from "node:child_process";
import fs from "node:fs/promises";
import path from "node:path";
import assert from "node:assert/strict";

const root = path.resolve("..");
const input = JSON.parse(await fs.readFile(path.join(root, ".intermediate/timeline/input.json"), "utf8"));
const code = execFileSync(path.join(root, "backend/.venv/bin/python"), ["-c", "from app.core.config import get_settings; print(get_settings().invite_code)"], { cwd: path.join(root, "backend"), encoding: "utf8" }).trim();
const browser = await chromium.launch({ channel: "chrome", args: ["--autoplay-policy=no-user-gesture-required"] });
const context = await browser.newContext({ ignoreHTTPSErrors: true });
const page = await context.newPage();
await page.goto("https://127.0.0.1:3444/login");
const result = await page.evaluate(async ({ code, input }) => {
  const request = async (url, body) => {
    const response = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  };
  await request("/api/v1/auth/login", { code });
  const call = await request("/api/v1/calls", { profile_id: input.profile_id, consent: true, request_id: crypto.randomUUID(), topic: "真实历史音轨重放，验证双方声音的实际合成与保存" });
  const audio = new AudioContext({ sampleRate: 16000 });
  await audio.resume();
  const microphone = await audio.decodeAudioData(Uint8Array.from(atob(input.user), value => value.charCodeAt(0)).buffer);
  await audio.audioWorklet.addModule("/pcm-worklet.js");
  const socket = new WebSocket(`${location.origin.replace("https:", "wss:")}/api/v1/calls/${call.id}/live`);
  const node = new AudioWorkletNode(audio, "pcm-capture", { numberOfInputs: 2 });
  const silent = audio.createGain(); silent.gain.value = 0; node.connect(silent); silent.connect(audio.destination);
  const bus = audio.createGain(); bus.connect(audio.destination); bus.connect(node, 0, 1);
  const source = audio.createBufferSource(); source.buffer = microphone; source.connect(node, 0, 0);
  let origin = 0, seq = 0, playAt = 0, stopping = false, received = 0, started = false;
  const sources = new Set();
  const frames = [];
  const event = (kind, extra = {}) => socket.send(JSON.stringify({ type: "timeline", kind, at_ms: Math.max(0, (audio.currentTime - origin) * 1000), wall_ms: Math.max(0, (audio.currentTime - origin) * 1000), ...extra }));
  const encode = values => {
    const bytes = new Uint8Array(values.length * 2), view = new DataView(bytes.buffer);
    values.forEach((value, index) => view.setInt16(index * 2, Math.max(-1, Math.min(1, value)) * (value < 0 ? 32768 : 32767), true));
    return btoa(String.fromCharCode(...bytes));
  };
  const completed = new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error("真实对话验证超时")), 70000);
    node.port.onmessage = message => {
      const data = message.data;
      if (!started || socket.readyState !== WebSocket.OPEN) return;
      if (data.type === "flushed") { socket.send(JSON.stringify({ type: "stop", capture_complete: true })); return; }
      if (data.frame / 16000 < origin) return;
      const frame = { seq: seq++, at_ms: (data.frame / 16000 - origin) * 1000, mixed: encode(data.mixed) };
      frames.push(frame);
      socket.send(JSON.stringify({ type: "audio", pcm: encode(data.samples), ...frame }));
    };
    socket.onmessage = message => {
      const value = JSON.parse(message.data);
      if (value.type === "ready") {
        origin = audio.currentTime;
        started = true;
        event("start");
        source.start();
        setTimeout(() => { stopping = true; event("stop"); sources.forEach(item => item.stop()); sources.clear(); node.port.postMessage("flush"); }, 30000);
      }
      if (value.type === "audio" && !stopping) {
        received++;
        const bytes = Uint8Array.from(atob(value.pcm), v => v.charCodeAt(0));
        const pcm = new DataView(bytes.buffer);
        const samples = new Float32Array(bytes.length / 2);
        for (let i = 0; i < samples.length; i++) samples[i] = pcm.getInt16(i * 2, true) / 32768;
        const buffer = audio.createBuffer(1, samples.length, 24000); buffer.copyToChannel(samples, 0);
        const player = audio.createBufferSource(); player.buffer = buffer; player.connect(bus); sources.add(player);
        player.onended = () => sources.delete(player);
        const when = playAt > audio.currentTime ? playAt : audio.currentTime + .12;
        player.start(when); playAt = when + buffer.duration;
        event("schedule", { at_ms: (when - origin) * 1000, duration_ms: buffer.duration * 1000, audio_id: received });
      }
      if (value.type === "interrupted") { event("interrupt"); sources.forEach(item => item.stop()); sources.clear(); playAt = 0; }
      if (value.type === "done") { clearTimeout(timeout); resolve(); }
      if (value.type === "error") { clearTimeout(timeout); reject(new Error(value.error.code)); }
    };
    socket.onerror = () => reject(new Error("WebSocket 失败"));
  });
  await completed;
  source.stop(); socket.close(); await audio.close();
  return { call_id: call.id, owner_id: input.owner_id, frames, received };
}, { code, input });
assert.ok(result.received > 0, "必须收到真实 AI 声音");
assert.ok(result.frames.length >= 1490, "应保存约 30 秒真实音频");
await fs.writeFile(path.join(root, ".intermediate/timeline/live.json"), JSON.stringify(result));
await browser.close();
console.log(`真实豆包与浏览器合成通过：${result.frames.length} 个采集分片，${result.received} 个实际播放的 AI 分片，正常结束。`);
