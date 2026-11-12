import { z } from "zod";
import type { CaptionAction } from "./call-captions";

const identifier = z.string().min(1).max(256);
export const callEventSchema = z.discriminatedUnion("type", [
  z.object({ type: z.literal("ready") }),
  z.object({ type: z.literal("opening_requested") }),
  z.object({ type: z.literal("ping") }),
  z.object({ type: z.literal("done") }),
  z.object({ type: z.literal("error"), error: z.object({ code: z.string(), message: z.string() }) }),
  z.object({ type: z.literal("user_text"), turn_id: identifier, text: z.string().max(64000), text_mode: z.literal("replace"), final: z.boolean(), status: z.enum(["candidate", "confirmed", "failed"]) }),
  z.object({ type: z.literal("assistant_text"), turn_id: identifier, text: z.string().max(64000), text_mode: z.literal("replace"), final: z.boolean(), source: z.enum(["model", "script"]), discarded: z.boolean().optional() }),
  z.object({ type: z.literal("assistant_tts"), turn_id: identifier, segment_id: identifier, text: z.string().max(64000), text_available: z.boolean(), source: z.literal("tts"), state: z.literal("queued"), discarded: z.boolean().optional() }),
  z.object({ type: z.literal("assistant_done"), turn_id: identifier, text_done: z.boolean(), audio_done: z.boolean(), interrupted: z.boolean().optional() }),
  z.object({ type: z.literal("audio"), pcm: z.string().min(1).max(12 * 1024 * 1024), turn_id: identifier, segment_id: identifier, audio_seq: z.number().int().nonnegative(), discarded: z.boolean().optional() }),
  z.object({ type: z.literal("interrupted"), assistant_turn_ids: z.array(identifier), user_turn_id: z.string().optional(), reason: z.enum(["user_speech", "response_canceled"]) }),
]);

export type CallEvent = z.infer<typeof callEventSchema>;
export type PlaybackActivity = "listening" | "thinking" | "speaking" | "paused";
type Details = Record<string, string | number>;
type CallPlaybackOptions = {
  caption: (action: CaptionAction) => void;
  timeline: (kind: string, details: Details, contextTime?: number) => void;
  activity: (state: PlaybackActivity) => void;
};
type Chunk = { source: AudioBufferSourceNode; id: number; sequence: number; turn: string; segment: string; start: number; end: number; generation: number; started: boolean; canceled: boolean };

export class CallPlayback {
  readonly bus: GainNode;
  readonly analyser: AnalyserNode;
  private chunks = new Map<number, Chunk>();
  private finishedTurns = new Set<string>();
  private startedSegments = new Set<string>();
  private canceledTurns = new Set<string>();
  private pendingTurns = new Set<string>();
  private generation = 0;
  private nextId = 0;
  private playAt = 0;
  private frame = 0;
  private previousFrame = 0;
  private stopped = false;
  private activity: PlaybackActivity = "listening";

  constructor(readonly context: AudioContext, private options: CallPlaybackOptions) {
    this.bus = context.createGain();
    this.analyser = context.createAnalyser();
    this.analyser.fftSize = 512;
    this.bus.connect(context.destination);
    this.bus.connect(this.analyser);
    context.addEventListener("statechange", this.update);
    document.addEventListener("visibilitychange", this.update);
  }

  connectRecording(node: AudioWorkletNode) { this.bus.connect(node, 0, 1); }

  receive(event: Exclude<CallEvent, { type: "ready" | "opening_requested" | "ping" | "done" | "error" }>) {
    if (this.stopped) return;
    if (event.type === "audio") { this.schedule(event); return; }
    if (event.type === "interrupted") {
      this.interrupt(event.reason === "user_speech" ? undefined : event.assistant_turn_ids, true, event.user_turn_id);
      return;
    }
    this.options.caption(event);
    if ((event.type === "assistant_text" || event.type === "assistant_tts") && !event.discarded && !this.canceledTurns.has(event.turn_id) && !this.finishedTurns.has(event.turn_id)) this.pendingTurns.add(event.turn_id);
    if (event.type === "assistant_done" && event.audio_done) { this.finishedTurns.add(event.turn_id); this.pendingTurns.delete(event.turn_id); }
    this.update();
  }

  private schedule(event: Extract<CallEvent, { type: "audio" }>) {
    if (event.discarded || this.canceledTurns.has(event.turn_id)) return;
    const bytes = Uint8Array.from(atob(event.pcm), letter => letter.charCodeAt(0));
    if (!bytes.length || bytes.length % 2) throw new Error("voice_audio_invalid");
    const view = new DataView(bytes.buffer);
    const samples = new Float32Array(bytes.length / 2);
    for (let index = 0; index < samples.length; index++) samples[index] = view.getInt16(index * 2, true) / 32768;
    const buffer = this.context.createBuffer(1, samples.length, 24000);
    buffer.copyToChannel(samples, 0);
    const source = this.context.createBufferSource();
    source.buffer = buffer;
    source.connect(this.bus);
    const start = this.playAt > this.context.currentTime ? this.playAt : this.context.currentTime + 0.12;
    const chunk: Chunk = { source, id: this.nextId++, sequence: event.audio_seq, turn: event.turn_id, segment: event.segment_id, start, end: start + buffer.duration, generation: this.generation, started: false, canceled: false };
    this.chunks.set(chunk.id, chunk);
    this.playAt = chunk.end;
    this.options.caption({ type: "audio_scheduled", audio_id: chunk.id, audio_seq: chunk.sequence, turn_id: chunk.turn, segment_id: chunk.segment, start_time: chunk.start, end_time: chunk.end, generation: chunk.generation });
    source.onended = () => {
      if (chunk.canceled || this.stopped || !this.chunks.has(chunk.id)) return;
      this.started(chunk);
      this.chunks.delete(chunk.id);
      source.disconnect();
      this.options.caption({ type: "playback_clock", current_time: this.context.currentTime });
      this.options.caption({ type: "audio_ended", audio_id: chunk.id, generation: chunk.generation });
      this.options.timeline("ended", this.details(chunk));
      this.update();
    };
    source.start(start);
    this.options.timeline("schedule", { ...this.details(chunk), duration_ms: buffer.duration * 1000 }, start);
    this.update();
  }

  private details(chunk: Chunk): Details {
    return { audio_id: chunk.id, audio_seq: chunk.sequence, turn_id: chunk.turn, segment_id: chunk.segment };
  }

  private started(chunk: Chunk) {
    if (chunk.started) return;
    chunk.started = true;
    const segment = JSON.stringify([chunk.turn, chunk.segment]);
    if (this.startedSegments.has(segment)) return;
    this.startedSegments.add(segment);
    this.options.timeline("playback_started", this.details(chunk), chunk.start);
  }

  private emitActivity(value: PlaybackActivity) {
    if (value === this.activity) return;
    this.activity = value;
    this.options.activity(value);
  }

  private update = () => {
    cancelAnimationFrame(this.frame);
    this.frame = 0;
    if (this.stopped) return;
    const now = this.context.currentTime;
    if (this.context.state !== "running" || document.visibilityState !== "visible") { this.emitActivity("paused"); return; }
    let speaking = false;
    for (const chunk of this.chunks.values()) if (now >= chunk.start && now < chunk.end) { this.started(chunk); speaking = true; }
    this.options.caption({ type: "playback_clock", current_time: now });
    this.emitActivity(speaking ? "speaking" : this.chunks.size || this.pendingTurns.size ? "thinking" : "listening");
    if (this.chunks.size) this.frame = requestAnimationFrame(this.tick);
  };

  private tick = (time: number) => {
    if (time - this.previousFrame < 50) { this.frame = requestAnimationFrame(this.tick); return; }
    this.previousFrame = time;
    this.update();
  };

  private interrupt(targets?: readonly string[], report = true, userTurn?: string) {
    if (targets !== undefined && targets.length === 0) return;
    const all = targets === undefined;
    const affected = new Set(targets ?? [...this.chunks.values()].map(chunk => chunk.turn));
    if (all) { for (const turn of this.pendingTurns) affected.add(turn); this.generation++; }
    for (const turn of affected) { this.canceledTurns.add(turn); this.pendingTurns.delete(turn); }
    this.options.caption({ type: "interrupted", assistant_turn_ids: [...affected], user_turn_id: userTurn, generation: this.generation, current_time: this.context.currentTime, reason: report ? all ? "user_speech" : "response_canceled" : "local_stop" });
    for (const chunk of this.chunks.values()) {
      if (!all && !affected.has(chunk.turn)) continue;
      chunk.canceled = true;
      chunk.source.stop();
      chunk.source.disconnect();
      this.chunks.delete(chunk.id);
    }
    if (report) {
      if (affected.size) for (const turn of affected) this.options.timeline("interrupt", { turn_id: turn });
      else this.options.timeline("interrupt", {});
    }
    this.playAt = Math.max(this.context.currentTime, ...[...this.chunks.values()].map(chunk => chunk.end));
    this.update();
  }

  stop() {
    if (this.stopped) return;
    this.interrupt(undefined, false);
    this.stopped = true;
    cancelAnimationFrame(this.frame);
    this.frame = 0;
    this.emitActivity("listening");
  }

  dispose() {
    this.stop();
    this.context.removeEventListener("statechange", this.update);
    document.removeEventListener("visibilitychange", this.update);
    this.bus.disconnect();
    this.analyser.disconnect();
  }
}
