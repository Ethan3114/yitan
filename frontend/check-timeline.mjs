import { chromium } from "@playwright/test";
import fs from "node:fs/promises";
import path from "node:path";
import assert from "node:assert/strict";

const root = path.resolve("..");
const input = JSON.parse(await fs.readFile(path.join(root, ".intermediate/timeline/input.json"), "utf8"));
const browser = await chromium.launch({ channel: "chrome" });
const context = await browser.newContext({ ignoreHTTPSErrors: true });
const page = await context.newPage();
await page.goto("https://127.0.0.1:3443/login");
const output = await page.evaluate(async ({ user, assistant }) => {
  const audio = new OfflineAudioContext(1, 16000 * 12, 16000);
  const decode = async text => audio.decodeAudioData(Uint8Array.from(atob(text), value => value.charCodeAt(0)).buffer);
  const mic = await decode(user), voice = await decode(assistant);
  await audio.audioWorklet.addModule("/pcm-worklet.js");
  const node = new AudioWorkletNode(audio, "pcm-capture", { numberOfInputs: 2 });
  node.connect(audio.destination);
  const blocks = [];
  node.port.onmessage = event => { if (event.data.type === "capture") blocks.push(event.data); };
  const microphone = audio.createGain(), playback = audio.createGain();
  microphone.connect(node, 0, 0); playback.connect(node, 0, 1);
  const add = (buffer, bus, start, end) => {
    const source = audio.createBufferSource(); source.buffer = buffer; source.connect(bus); source.start(start); source.stop(end);
  };
  add(mic, microphone, 0, 2);
  add(mic, microphone, 8, 9);
  // 持续存在的真实麦克风音轨通过音量门保留停顿，音源仍来自真实录音。
  const continuous = audio.createBufferSource(); continuous.buffer = mic;
  const muted = audio.createGain(); muted.gain.value = 0; continuous.connect(muted); muted.connect(node, 0, 0); continuous.start();
  add(voice, playback, 3, 4);
  add(voice, playback, 6, 12);
  await audio.startRendering();
  await new Promise(resolve => setTimeout(resolve, 100));
  const samples = new Float32Array(16000 * 12);
  for (const block of blocks) samples.set(block.mixed, block.frame);
  const microphoneSamples = mic.getChannelData(0), voiceSamples = voice.getChannelData(0);
  let maximumError = 0;
  for (let frame = 0; frame < samples.length; frame++) {
    const t = frame / 16000;
    let expected = 0;
    if (t < 2) expected += microphoneSamples[frame] || 0;
    if (t >= 8 && t < 9) expected += microphoneSamples[frame - 8 * 16000] || 0;
    if (t >= 3 && t < 4) expected += voiceSamples[frame - 3 * 16000] || 0;
    if (t >= 6) expected += voiceSamples[frame - 6 * 16000] || 0;
    maximumError = Math.max(maximumError, Math.abs(samples[frame] - expected * .5));
  }
  const encode = values => {
    const bytes = new Uint8Array(values.length * 2), view = new DataView(bytes.buffer);
    values.forEach((value, index) => view.setInt16(index * 2, Math.round(Math.max(-1, Math.min(1, value)) * 32767), true));
    return btoa(String.fromCharCode(...bytes));
  };
  return { maximumError, frames: blocks.map((block, seq) => ({ seq, at_ms: block.frame / 16, mixed: encode(block.mixed) })), events: [{ kind: "start", at_ms: 0, wall_ms: 0 }, { kind: "schedule", at_ms: 3000, wall_ms: 3000, duration_ms: voice.duration * 1000, audio_id: 0 }, { kind: "interrupt", at_ms: 4000, wall_ms: 4000 }, { kind: "schedule", at_ms: 6000, wall_ms: 6000, duration_ms: voice.duration * 1000, audio_id: 1 }, { kind: "stop", at_ms: 12000, wall_ms: 12000 }] };
}, input);
assert.equal(output.frames.length, 600);
assert.ok(output.maximumError < 0.00001, `合成误差 ${output.maximumError}`);
await fs.writeFile(path.join(root, ".intermediate/timeline/render.json"), JSON.stringify(output));
await browser.close();
console.log(`真实音轨经浏览器 AudioWorklet 合成：600 个分片，逐采样最大误差 ${output.maximumError}；静音、重叠和 4 秒打断验证通过。`);
