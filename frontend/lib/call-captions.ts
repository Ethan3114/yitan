export type CaptionStatus = "recognizing" | "confirmed" | "failed" | "queued" | "speaking" | "spoken" | "interrupted";
export type CaptionEntry = {
  readonly id: string;
  readonly role: "user" | "assistant";
  readonly source: "asr" | "tts" | "model" | "script";
  readonly reference: boolean;
  readonly text: string;
  readonly status: CaptionStatus;
  readonly turnId: string;
  readonly segmentId: string | null;
  readonly final: boolean;
  readonly playbackStarted: boolean;
  readonly textAvailable: boolean;
};

type ModelDraft = { readonly text: string; readonly final: boolean; readonly source: "model" | "script" };
type Segment = { readonly turnId: string; readonly segmentId: string; readonly scheduled: number; readonly ended: number; readonly started: boolean };
type AudioChunk = {
  readonly audioId: number;
  readonly audioSeq: number;
  readonly turnId: string;
  readonly segmentId: string;
  readonly generation: number;
  readonly start: number;
  readonly end: number;
  readonly started: boolean;
};
type TurnProgress = { readonly textDone: boolean; readonly audioDone: boolean };

export type CaptionState = {
  readonly entries: readonly CaptionEntry[];
  readonly generation: number;
  readonly currentTime: number;
  readonly modelDrafts: ReadonlyMap<string, ModelDraft>;
  readonly segments: ReadonlyMap<string, Segment>;
  readonly audio: ReadonlyMap<number, AudioChunk>;
  readonly turns: ReadonlyMap<string, TurnProgress>;
  readonly interruptedTurns: ReadonlySet<string>;
};

export type CaptionAction =
  | { type: "user_text"; turn_id: string; text: string; text_mode: "replace"; final: boolean; status: "candidate" | "confirmed" | "failed" }
  | { type: "assistant_text"; turn_id: string; text: string; text_mode: "replace"; final: boolean; source: "model" | "script"; discarded?: boolean }
  | { type: "assistant_tts"; turn_id: string; segment_id: string; text: string; source: "tts"; text_available: boolean; state: "queued"; discarded?: boolean }
  | { type: "assistant_done"; turn_id: string; text_done: boolean; audio_done: boolean; interrupted?: boolean }
  | { type: "audio_scheduled"; audio_id: number; audio_seq: number; turn_id: string; segment_id: string; start_time: number; end_time: number; generation: number; discarded?: boolean }
  | { type: "playback_clock"; current_time: number }
  | { type: "audio_ended"; audio_id: number; generation: number }
  | { type: "interrupted"; assistant_turn_ids: readonly string[]; user_turn_id?: string; reason?: "user_speech" | "response_canceled" | "local_stop"; generation: number; current_time?: number }
  | { type: "reset"; generation?: number };

export function createCaptionState(generation = 0): CaptionState {
  return { entries: [], generation, currentTime: 0, modelDrafts: new Map(), segments: new Map(), audio: new Map(), turns: new Map(), interruptedTurns: new Set() };
}

export const initialCaptionState = createCaptionState();

function segmentKey(turnId: string, segmentId: string) {
  return JSON.stringify([turnId, segmentId]);
}
function assistantId(turnId: string, segmentId: string) {
  return JSON.stringify(["assistant", turnId, segmentId]);
}
function sameEntry(left: CaptionEntry, right: CaptionEntry) {
  return left.id === right.id && left.role === right.role && left.source === right.source && left.reference === right.reference && left.text === right.text && left.status === right.status && left.turnId === right.turnId && left.segmentId === right.segmentId && left.final === right.final && left.playbackStarted === right.playbackStarted && left.textAvailable === right.textAvailable;
}
function putEntry(state: CaptionState, entry: CaptionEntry): CaptionState {
  const index = state.entries.findIndex(previous => previous.id === entry.id);
  if (index < 0) return { ...state, entries: [...state.entries, entry] };
  if (sameEntry(state.entries[index], entry)) return state;
  const entries = [...state.entries];
  entries[index] = entry;
  return { ...state, entries };
}
function hasStartedWithoutTts(state: CaptionState, turnId: string): boolean {
  return [...state.segments.values()].some(segment => segment.turnId === turnId && segment.started && !state.entries.some(entry => entry.id === assistantId(turnId, segment.segmentId) && entry.textAvailable));
}
function turnPlaybackComplete(state: CaptionState, turnId: string): boolean {
  if (!state.turns.get(turnId)?.audioDone) return false;
  const segments = [...state.segments.values()].filter(segment => segment.turnId === turnId);
  const scheduled = segments.reduce((total, segment) => total + segment.scheduled, 0);
  return scheduled > 0 && segments.every(segment => segment.scheduled === segment.ended);
}
function reconcileReference(state: CaptionState, turnId: string): CaptionState {
  const draft = state.modelDrafts.get(turnId);
  if (!draft?.text || !hasStartedWithoutTts(state, turnId)) return state;
  const segments = [...state.segments.values()].filter(segment => segment.turnId === turnId);
  const scheduled = segments.reduce((total, segment) => total + segment.scheduled, 0);
  const ended = segments.reduce((total, segment) => total + segment.ended, 0);
  const audioEnded = scheduled > 0 && scheduled === ended && state.turns.get(turnId)?.audioDone;
  const status: CaptionStatus = state.interruptedTurns.has(turnId) ? "interrupted" : audioEnded ? "confirmed" : "speaking";
  return putEntry(state, { id: JSON.stringify(["assistant_reference", turnId]), role: "assistant", source: draft.source, reference: true, text: draft.text, status, turnId, segmentId: null, final: draft.final, playbackStarted: true, textAvailable: true });
}
function reconcileSegment(state: CaptionState, key: string): CaptionState {
  const segment = state.segments.get(key);
  if (!segment) return state;
  const entry = state.entries.find(item => item.id === assistantId(segment.turnId, segment.segmentId));
  if (!entry) return reconcileReference(state, segment.turnId);
  const interrupted = state.interruptedTurns.has(segment.turnId);
  const played = segment.started && segment.scheduled > 0 && segment.ended === segment.scheduled && state.turns.get(segment.turnId)?.audioDone;
  const status: CaptionStatus = interrupted ? entry.status === "spoken" ? "spoken" : "interrupted" : played ? "spoken" : segment.started ? "speaking" : "queued";
  return reconcileReference(putEntry(state, { ...entry, status, playbackStarted: segment.started }), segment.turnId);
}
function advanceClock(state: CaptionState, currentTime: number): CaptionState {
  if (!Number.isFinite(currentTime) || currentTime < 0) throw new Error("caption_clock_invalid");
  let audio: Map<number, AudioChunk> | undefined;
  let segments: Map<string, Segment> | undefined;
  const changed = new Set<string>();
  for (const [id, chunk] of state.audio) {
    if (chunk.started || chunk.generation !== state.generation || currentTime < chunk.start || state.interruptedTurns.has(chunk.turnId)) continue;
    audio ??= new Map(state.audio);
    segments ??= new Map(state.segments);
    audio.set(id, { ...chunk, started: true });
    const key = segmentKey(chunk.turnId, chunk.segmentId);
    const segment = segments.get(key);
    if (segment && !segment.started) { segments.set(key, { ...segment, started: true }); changed.add(key); }
  }
  if (!audio && state.currentTime === currentTime) return state;
  let next: CaptionState = { ...state, currentTime, audio: audio ?? state.audio, segments: segments ?? state.segments };
  for (const key of changed) next = reconcileSegment(next, key);
  return next;
}
function interruptTurns(state: CaptionState, turnIds: ReadonlySet<string>, generation: number): CaptionState {
  const affected = new Set([...turnIds].filter(id => !turnPlaybackComplete(state, id)));
  const interruptedTurns = new Set(state.interruptedTurns);
  for (const id of affected) interruptedTurns.add(id);
  const audio = new Map(state.audio);
  for (const [id, chunk] of audio) if (affected.has(chunk.turnId) || chunk.generation !== generation) audio.delete(id);
  let next: CaptionState = { ...state, generation, audio, interruptedTurns };
  for (const [key, segment] of state.segments) if (affected.has(segment.turnId)) next = reconcileSegment(next, key);
  return next;
}

export function captionReducer(state: CaptionState, action: CaptionAction): CaptionState {
  switch (action.type) {
    case "reset":
      return createCaptionState(action.generation ?? state.generation + 1);
    case "user_text": {
      const id = JSON.stringify(["user", action.turn_id]);
      const previous = state.entries.find(entry => entry.id === id);
      if (previous?.final && !action.final) return state;
      const status: CaptionStatus = action.status === "failed" ? "failed" : action.final || action.status === "confirmed" ? "confirmed" : "recognizing";
      const text = action.status === "failed" && !action.text && previous ? previous.text : action.text;
      return putEntry(state, { id, role: "user", source: "asr", reference: false, text, status, turnId: action.turn_id, segmentId: null, final: action.final, playbackStarted: false, textAvailable: text.length > 0 });
    }
    case "assistant_text": {
      if (action.discarded || state.interruptedTurns.has(action.turn_id)) return state;
      const previous = state.modelDrafts.get(action.turn_id);
      if (previous?.source === "model" && action.source === "script") return state;
      if (previous?.source === action.source) {
        if (previous.final && !action.final || previous.text === action.text && previous.final === action.final) return state;
      }
      const modelDrafts = new Map(state.modelDrafts);
      modelDrafts.set(action.turn_id, { text: action.text, final: action.final, source: action.source });
      return reconcileReference({ ...state, modelDrafts }, action.turn_id);
    }
    case "assistant_tts": {
      if (action.discarded || state.interruptedTurns.has(action.turn_id)) return state;
      const key = segmentKey(action.turn_id, action.segment_id);
      const segments = new Map(state.segments);
      if (!segments.has(key)) segments.set(key, { turnId: action.turn_id, segmentId: action.segment_id, scheduled: 0, ended: 0, started: false });
      const id = assistantId(action.turn_id, action.segment_id);
      const previous = state.entries.find(entry => entry.id === id);
      const textAvailable = action.text_available || previous?.textAvailable === true;
      const text = action.text_available ? action.text : previous?.text ?? "";
      const next = putEntry({ ...state, segments }, { id, role: "assistant", source: "tts", reference: false, text, status: "queued", turnId: action.turn_id, segmentId: action.segment_id, final: textAvailable, playbackStarted: false, textAvailable });
      return reconcileSegment(next, key);
    }
    case "audio_scheduled": {
      if (action.discarded || action.generation !== state.generation || state.interruptedTurns.has(action.turn_id)) return state;
      if (![action.start_time, action.end_time].every(Number.isFinite) || action.start_time < 0 || action.end_time <= action.start_time) throw new Error("caption_playback_interval_invalid");
      if (state.audio.has(action.audio_id)) throw new Error("caption_audio_id_duplicate");
      const key = segmentKey(action.turn_id, action.segment_id);
      const previous = state.segments.get(key) ?? { turnId: action.turn_id, segmentId: action.segment_id, scheduled: 0, ended: 0, started: false };
      const segments = new Map(state.segments);
      segments.set(key, { ...previous, scheduled: previous.scheduled + 1 });
      const audio = new Map(state.audio);
      audio.set(action.audio_id, { audioId: action.audio_id, audioSeq: action.audio_seq, turnId: action.turn_id, segmentId: action.segment_id, generation: action.generation, start: action.start_time, end: action.end_time, started: false });
      return reconcileSegment({ ...state, audio, segments }, key);
    }
    case "playback_clock":
      return advanceClock(state, action.current_time);
    case "audio_ended": {
      if (action.generation !== state.generation) return state;
      const chunk = state.audio.get(action.audio_id);
      if (!chunk || chunk.generation !== action.generation || state.interruptedTurns.has(chunk.turnId)) return state;
      const key = segmentKey(chunk.turnId, chunk.segmentId);
      const segment = state.segments.get(key);
      if (!segment) throw new Error("caption_audio_segment_missing");
      const audio = new Map(state.audio);
      audio.delete(action.audio_id);
      const segments = new Map(state.segments);
      segments.set(key, { ...segment, started: true, ended: segment.ended + 1 });
      return reconcileSegment({ ...state, audio, segments }, key);
    }
    case "assistant_done": {
      const previous = state.turns.get(action.turn_id);
      const turns = new Map(state.turns);
      turns.set(action.turn_id, { textDone: previous?.textDone === true || action.text_done, audioDone: previous?.audioDone === true || action.audio_done });
      let next: CaptionState = { ...state, turns };
      if (action.interrupted) next = interruptTurns(next, new Set([action.turn_id]), state.generation);
      for (const [key, segment] of next.segments) if (segment.turnId === action.turn_id) next = reconcileSegment(next, key);
      return next;
    }
    case "interrupted": {
      if (action.generation < state.generation) return state;
      if (action.reason === "response_canceled" && action.assistant_turn_ids.length === 0) return state;
      const advanced = advanceClock(state, action.current_time ?? state.currentTime);
      const turns = new Set(action.assistant_turn_ids);
      if (action.reason !== "response_canceled") {
        for (const chunk of advanced.audio.values()) turns.add(chunk.turnId);
        for (const entry of advanced.entries) if (entry.role === "assistant" && (entry.status === "queued" || entry.status === "speaking")) turns.add(entry.turnId);
        for (const turnId of advanced.modelDrafts.keys()) turns.add(turnId);
      }
      return interruptTurns(advanced, turns, action.reason === "response_canceled" ? state.generation : action.generation);
    }
  }
}

export function selectVisibleCaptions(state: CaptionState): readonly CaptionEntry[] {
  return state.entries.filter(entry => entry.role === "user" ? entry.textAvailable || entry.status === "failed" : entry.playbackStarted && entry.textAvailable && (!entry.reference || hasStartedWithoutTts(state, entry.turnId)));
}

export function selectCurrentCaption(state: CaptionState): CaptionEntry | null {
  const visible = selectVisibleCaptions(state);
  const active = [...state.audio.values()].filter(chunk => chunk.generation === state.generation && !state.interruptedTurns.has(chunk.turnId) && state.currentTime >= chunk.start && state.currentTime < chunk.end);
  for (const chunk of active.toReversed()) {
    const tts = visible.find(entry => entry.id === assistantId(chunk.turnId, chunk.segmentId));
    if (tts) return tts;
    const reference = visible.find(entry => entry.reference && entry.turnId === chunk.turnId);
    if (reference) return reference;
  }
  return visible.at(-1) ?? null;
}

export function selectAssistantPlaybackActive(state: CaptionState, currentTime: number): boolean {
  return [...state.audio.values()].some(chunk => chunk.generation === state.generation && !state.interruptedTurns.has(chunk.turnId) && currentTime >= chunk.start && currentTime < chunk.end);
}
