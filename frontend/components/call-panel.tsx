"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { Mic, PhoneOff } from "lucide-react";
import { v4 as uuid } from "uuid";
import { api, messageFor, wsUrl } from "@/lib/api";
import { Status } from "./shell";
import { track } from "@/lib/analytics";

type Props = { profileId: string; elderName?: string; photoId?: string; voiceReady: boolean; onFinished: () => void };
type Phase = "idle" | "connecting" | "live" | "saving" | "failed";
export function CallPanel({ profileId, elderName, photoId, voiceReady, onFinished }: Props) {
  const [consent, setConsent] = useState(false);
  const [topic, setTopic] = useState("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [error, setError] = useState("");
  const [elapsed, setElapsed] = useState(0);
  const resources = useRef<{ socket?: WebSocket; context?: AudioContext; stream?: MediaStream; node?: AudioWorkletNode; sources: Set<AudioBufferSourceNode>; playAt: number; callId?: string }>({ sources: new Set(), playAt: 0 });
  const busy = useRef(false);
  const terminal = useRef(false);
  const requestId = useRef("");
  const mounted = useRef(false);
  const clock = useRef({ origin: 0, wall: 0, gap: 0, gaps: [] as { frame: number; total: number }[], suspended: 0, seq: 0, audioId: 0, stopping: false, finished: false });
  const timeline = useCallback((kind: string, extra: Record<string, number> = {}) => {
    const r = resources.current, c = clock.current;
    if (r.socket?.readyState === WebSocket.OPEN && r.context) r.socket.send(JSON.stringify({ type: "timeline", kind, at_ms: Math.max(0, (r.context.currentTime - c.origin) * 1000 + c.gap), wall_ms: Math.max(0, performance.now() - c.wall), ...extra }));
  }, []);
  const stopCapture = useCallback(() => {
    const r = resources.current, c = clock.current;
    if (c.stopping) return;
    c.stopping = true;
    timeline("stop");
    r.sources.forEach(source => source.stop()); r.sources.clear();
    r.node?.port.postMessage("flush");
    window.setTimeout(() => {
      if (c.finished || r.socket?.readyState !== WebSocket.OPEN) return;
      c.finished = true;
      r.stream?.getTracks().forEach(track => track.stop());
      r.socket.send(JSON.stringify({ type: "stop", capture_complete: false }));
    }, 1000);
  }, [timeline]);
  const cleanup = useCallback(() => {
    const r = resources.current;
    if (!clock.current.finished) timeline("disconnect");
    clock.current.finished = true;
    r.stream?.getTracks().forEach(track => track.stop());
    r.node?.disconnect();
    r.sources.forEach(source => source.stop()); r.sources.clear();
    if (r.context && r.context.state !== "closed") void r.context.close();
    r.socket?.close();
  }, [timeline]);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; terminal.current = true; cleanup(); };
  }, [cleanup]);
  useEffect(() => {
    if (phase !== "live") return;
    const timer = setInterval(() => setElapsed(value => value + 1), 1000);
    return () => clearInterval(timer);
  }, [phase]);
  useEffect(() => {
    if (phase !== "live") return;
    const saveWhenHidden = () => {
      if (document.visibilityState !== "hidden") return;
      setPhase("saving");
      stopCapture();
    };
    document.addEventListener("visibilitychange", saveWhenHidden);
    return () => document.removeEventListener("visibilitychange", saveWhenHidden);
  }, [phase, stopCapture]);
  async function start() {
    if (busy.current) return;
    if (!consent) { setError("请由老人本人勾选同意录音后再开始。"); return; }
    if (!voiceReady) { setError("语音服务尚未准备好，请联系管理家属。"); return; }
    if (!navigator.mediaDevices?.getUserMedia) { setError("麦克风需要安全连接，请通过忆谈的 HTTPS 地址打开。"); return; }
    busy.current = true; terminal.current = false; setPhase("connecting"); setError(""); setElapsed(0);
    track("recording_consent");
    try {
      const context = new AudioContext({ sampleRate: 16000 });
      resources.current.context = context;
      await context.resume();
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 } });
      track("microphone_allowed");
      if (!mounted.current) { stream.getTracks().forEach(track => track.stop()); return; }
      const r = resources.current;
      r.stream = stream; r.context = context; r.playAt = 0;
      await context.audioWorklet.addModule("/pcm-worklet.js");
      if (!mounted.current) return;
      if (!requestId.current) requestId.current = uuid();
      const opened = await api<{ id: string }>("/api/v1/calls", { method: "POST", body: JSON.stringify({ profile_id: profileId, consent: true, request_id: requestId.current, photo_id: photoId, topic }) });
      r.callId = opened.id;
      if (!mounted.current) { await api(`/api/v1/calls/${opened.id}/abandon`, { method: "POST" }); return; }
      const socket = new WebSocket(wsUrl(opened.id)); r.socket = socket;
      clock.current = { origin: context.currentTime, wall: performance.now(), gap: 0, gaps: [], suspended: 0, seq: 0, audioId: 0, stopping: false, finished: false };
      context.onstatechange = () => {
        const c = clock.current;
        if (context.state !== "running" && context.state !== "closed" && !c.suspended) { c.suspended = performance.now(); timeline("suspend"); }
        if (context.state === "running" && c.suspended) { c.gap += performance.now() - c.suspended; c.gaps.push({ frame: context.currentTime * context.sampleRate, total: c.gap }); c.suspended = 0; timeline("resume"); }
      };
      const source = context.createMediaStreamSource(stream);
      const node = new AudioWorkletNode(context, "pcm-capture", { numberOfInputs: 2 }); r.node = node;
      const playbackBus = context.createGain();
      playbackBus.connect(context.destination);
      playbackBus.connect(node, 0, 1);
      source.connect(node);
      const silent = context.createGain(); silent.gain.value = 0;
      node.connect(silent); silent.connect(context.destination);
      const encode = (samples: Float32Array) => {
        const ratio = context.sampleRate / 16000;
        const size = Math.floor(samples.length / ratio);
        const bytes = new Uint8Array(size * 2); const view = new DataView(bytes.buffer);
        for (let i = 0; i < size; i++) { const sample = Math.max(-1, Math.min(1, samples[Math.floor(i * ratio)])); view.setInt16(i * 2, sample < 0 ? sample * 32768 : sample * 32767, true); }
        return btoa(String.fromCharCode(...bytes));
      };
      node.port.onmessage = (event: MessageEvent<{ type: string; frame: number; samples: Float32Array; mixed: Float32Array }>) => {
        if (socket.readyState !== WebSocket.OPEN || terminal.current) return;
        const c = clock.current;
        if (c.finished) return;
        if (event.data.type === "flushed") {
          c.finished = true;
          r.stream?.getTracks().forEach(track => track.stop());
          socket.send(JSON.stringify({ type: "stop", capture_complete: true }));
          return;
        }
        const gap = c.gaps.findLast(item => item.frame <= event.data.frame)?.total || 0;
        const at_ms = Math.max(0, (event.data.frame / context.sampleRate - c.origin) * 1000 + gap);
        socket.send(JSON.stringify({ type: "audio", pcm: encode(event.data.samples), mixed: encode(event.data.mixed), at_ms, seq: c.seq++ }));
      };
      socket.onmessage = event => {
        const data = JSON.parse(String(event.data)) as { type: string; pcm?: string; error?: { message: string } };
        if (data.type === "ready") { timeline("start"); setPhase("live"); }
        if (data.type === "audio" && data.pcm && !terminal.current && !clock.current.stopping) {
          const bytes = Uint8Array.from(atob(data.pcm), letter => letter.charCodeAt(0));
          const view = new DataView(bytes.buffer); const floats = new Float32Array(bytes.length / 2);
          for (let i = 0; i < floats.length; i++) floats[i] = view.getInt16(i * 2, true) / 32768;
          const buffer = context.createBuffer(1, floats.length, 24000); buffer.copyToChannel(floats, 0);
          const playback = context.createBufferSource(); playback.buffer = buffer; playback.connect(playbackBus);
          const audio_id = clock.current.audioId++;
          r.sources.add(playback); playback.onended = () => { r.sources.delete(playback); if (!clock.current.stopping) timeline("ended", { audio_id }); };
          const when = r.playAt > context.currentTime ? r.playAt : context.currentTime + 0.12;
          playback.start(when); r.playAt = when + buffer.duration;
          timeline("schedule", { audio_id, at_ms: Math.max(0, (when - clock.current.origin) * 1000 + clock.current.gap), duration_ms: buffer.duration * 1000 });
        }
        if (data.type === "interrupted") { timeline("interrupt"); r.sources.forEach(item => item.stop()); r.sources.clear(); r.playAt = 0; }
        if (data.type === "done") { terminal.current = true; cleanup(); busy.current = false; onFinished(); }
        if (data.type === "error") { terminal.current = true; cleanup(); busy.current = false; requestId.current = ""; setPhase("failed"); setError(data.error?.message || "访谈中断了，请返回回忆录查看已保存内容。"); }
      };
      socket.onclose = () => {
        if (terminal.current) return;
        terminal.current = true; cleanup(); busy.current = false; requestId.current = "";
        setPhase("failed"); setError("连接已断开。请返回回忆录，查看本次访谈的保存情况。");
      };
    } catch (cause) {
      if (cause instanceof DOMException && cause.name === "NotAllowedError") track("microphone_denied");
      if (!mounted.current) return;
      terminal.current = true; cleanup(); busy.current = false; setPhase("failed"); setError(messageFor(cause));
      if (resources.current.callId) await api(`/api/v1/calls/${resources.current.callId}/abandon`, { method: "POST" });
      requestId.current = "";
    }
  }
  function stop() {
    if (phase !== "live") return;
    setPhase("saving");
    stopCapture();
  }
  const active = phase === "live" || phase === "saving" || phase === "connecting";
  return <section>
    <div className="call-orb"><Mic size={52} strokeWidth={1.2} /></div><div className="call-heading"><h2>{elderName}，我在听</h2><p>{phase === "live" ? "慢慢讲，随时可以打断我。" : phase === "saving" ? "正在保存这次访谈，请稍候。" : phase === "connecting" ? "正在接通，请稍候。" : "我是 AI 访谈者，陪您回忆走过的日子。"}</p></div>
    {active ? <div className="call-timer" aria-label="通话时长">{String(Math.floor(elapsed / 60)).padStart(2, "0")}:{String(elapsed % 60).padStart(2, "0")}</div> : <><label className="field">{photoId ? "这张照片有什么故事？" : "今天想聊什么？"}<textarea value={topic} onChange={event => setTopic(event.target.value)} maxLength={500} placeholder={photoId ? "照片里的人、地点，或您想聊的事情" : "可以不填，想起什么就聊什么"} /></label><label className="consent"><input type="checkbox" checked={consent} onChange={event => setConsent(event.target.checked)} /><span>我本人同意录音，用来整理回忆录。随时可以结束访谈。</span></label></>}
    <Status error={error} />
    <button type="button" className="primary" disabled={phase === "connecting" || phase === "saving"} onClick={phase === "live" ? stop : () => void start()}>{phase === "live" ? <PhoneOff /> : <Mic />}{phase === "live" ? "结束这次访谈" : phase === "saving" ? "正在保存…" : phase === "connecting" ? "正在接通…" : "开始聊聊"}</button>
    {phase === "failed" ? <button type="button" className="secondary section" onClick={onFinished}>返回回忆录查看</button> : null}
    <p className="talk-note">录音会保存下来，留给您和获得授权的家人。访谈时请保持这个页面打开，切换到后台会结束并保存。</p>
  </section>;
}
