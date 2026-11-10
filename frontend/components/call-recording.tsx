import { API_BASE } from "@/lib/api";

export function CallRecording({ callId, kinds }: { callId: string; kinds: string[] }) {
  const complete = kinds.includes("conversation");
  return <div>
    {complete ? <div><p className="small-text">双方对话录音 · 按实际播放顺序保存</p><audio controls preload="none" className="audio" src={`${API_BASE}/api/v1/calls/${callId}/audio/conversation`} /></div> : <p className="small-text muted">这次访谈未保存合成对话录音，可以分别收听已保存的声音。</p>}
    <details><summary>分别收听原始音轨</summary>{kinds.filter(kind => kind === "user" || kind === "assistant").map(kind => <div key={kind}><span className="small-text">{kind === "user" ? "麦克风录音" : "AI 返回的原始声音（包含可能未播放的内容）"}</span><audio controls preload="none" className="audio" src={`${API_BASE}/api/v1/calls/${callId}/audio/${kind}`} /></div>)}</details>
  </div>;
}
