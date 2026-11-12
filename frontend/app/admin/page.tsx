"use client";
import { FormEvent, useState } from "react";
import { z } from "zod";
import { Shell, Status } from "@/components/shell";
import { useResource } from "@/lib/use-resource";
import { api, messageFor } from "@/lib/api";
import "./panel.css";
import { useHydrated } from "@/lib/use-hydrated";

const metricsSchema = z.object({
  as_of: z.string(), latest_event_at: z.string().nullable(), observing_cohorts: z.number(), excluded_admin_accounts: z.number(),
  funnel: z.array(z.object({ event: z.string(), stage: z.string(), label: z.string(), count: z.number(), previous: z.number(), rate: z.number().nullable(), not_completed: z.number() })),
  retention: z.array(z.object({ day: z.number(), eligible: z.number(), retained: z.number(), rate: z.number().nullable() })),
  activity: z.record(z.string(), z.number()),
  failures: z.array(z.object({ stage: z.string(), code: z.string(), count: z.number() })),
  tasks: z.array(z.object({ status: z.string(), count: z.number() })),
});
const accountsSchema = z.object({ items: z.array(z.object({ id: z.string(), username: z.string(), role: z.string(), enabled: z.boolean(), created_at: z.string() })) });
const invitesSchema = z.object({ items: z.array(z.object({ id: z.string(), created_at: z.string(), expires_at: z.string(), used: z.boolean(), revoked: z.boolean() })) });
const percent = (value: number | null) => value === null ? "暂无" : `${(value * 100).toFixed(1)}%`;
const dateText = (value: string) => new Date(value.endsWith("Z") || /[+-]\d\d:\d\d$/.test(value) ? value : value + "Z").toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" });

export default function Admin() {
  const hydrated = useHydrated();
  const [tab, setTab] = useState("overview");
  const [range, setRange] = useState("");
  const [selectedStage, setSelectedStage] = useState<number | null>(null);
  const metrics = useResource(`/api/v1/admin/metrics${range}`, metricsSchema);
  const accounts = useResource("/api/v1/admin/accounts", accountsSchema);
  const invites = useResource("/api/v1/admin/invitations", invitesSchema);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [secret, setSecret] = useState<{ label: string; value: string } | null>(null);
  function filter(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const query = new URLSearchParams();
    if (form.get("start")) query.set("start", String(form.get("start")));
    if (form.get("end")) query.set("end", String(form.get("end")));
    setRange(query.size ? `?${query}` : ""); setSelectedStage(null);
  }
  async function invitation() {
    setBusy(true); setMessage("");
    try { const result = await api<{ invitation: string }>("/api/v1/admin/invitations", { method: "POST" }); setSecret({ label: "一次性邀请码 · 七天有效", value: result.invitation }); invites.reload(); }
    catch (cause) { setMessage(messageFor(cause)); }
    finally { setBusy(false); }
  }
  async function recovery(id: string) {
    if (!window.confirm("已核实对方身份，并为这个账户创建三十分钟有效的恢复码？")) return;
    setBusy(true); setMessage("");
    try { const result = await api<{ token: string }>(`/api/v1/admin/accounts/${id}/recovery`, { method: "POST" }); setSecret({ label: "一次性恢复码 · 三十分钟有效", value: result.token }); }
    catch (cause) { setMessage(messageFor(cause)); }
    finally { setBusy(false); }
  }
  const data = metrics.data;
  return <Shell title="忆谈管理" subtitle="看见每一步，认真改善体验" back="/account" nav={false}><div className="admin-panel">
    <nav className="admin-tabs" aria-label="后台页面">{[["overview", "使用概况"], ["funnel", "分阶段转化"], ["retention", "用户留存"], ["runtime", "运行监控"], ["accounts", "账户邀请"]].map(([value, label]) => <button key={value} type="button" disabled={!hydrated} aria-pressed={tab === value} onClick={() => setTab(value)}>{label}</button>)}</nav>
    <form className="admin-filter" onSubmit={filter}><label>开始日期<input name="start" type="date" /></label><label>结束日期<input name="end" type="date" /></label><button className="secondary" type="submit">查看数据</button></form>
    <Status loading={!data && !metrics.error} error={metrics.error || message} unauthorized={metrics.unauthorized} retry={metrics.reload} />
    {data ? <><p className="muted small-text">北京时间 · 更新于 {dateText(data.as_of)}</p>
      {!data.latest_event_at ? <div className="notice">所选时间内尚未收到用户行为数据。开始真实使用后，这里会显示实际人数。</div> : <p className="muted small-text">最近收到事件：{dateText(data.latest_event_at)}</p>}
      {tab === "overview" ? <><section className="admin-kpis">{[["引导访客", data.activity.guide_viewed || 0], ["新注册家庭", data.activity.account_registered || 0], ["开始访谈家庭", data.activity.call_connected || 0], ["阅读成稿家庭", data.activity.memoir_read || 0]].map(([label, value]) => <article className="card" key={label}><p>{label}</p><strong>{value}</strong></article>)}</section><section className="card"><h2>找到用户停下的位置</h2><p>查看引导、注册、建档、讲述和成书各阶段的人数，以及未继续和失败的情况。</p><button className="text-link" onClick={() => setTab("funnel")}>查看完整转化过程 →</button></section></> : null}
      {tab === "funnel" ? <section className="card"><h2>从第一次翻书，到读到自己的故事</h2><p className="muted small-text">按首次进入引导的访客统计，观察七天，严格按顺序完成。{data.observing_cohorts} 个身份仍在观察期内。匿名访客按浏览器识别，注册后按家庭账号合并。</p><ol className="admin-funnel">{data.funnel.map((item, index) => <li key={`${item.event}-${item.stage}`}><button onClick={() => setSelectedStage(index)} aria-expanded={selectedStage === index}><span>{index + 1}. {item.label}</span><strong>{item.count}</strong></button><div className="admin-bar"><span style={{ width: `${data.funnel[0].count ? item.count / data.funnel[0].count * 100 : 0}%` }} /></div><p className="small-text muted">上一步转化 {percent(item.rate)} · 未继续 {item.not_completed}</p>{selectedStage === index ? <div className="notice">进入上一步 {item.previous}，完成本步 {item.count}。观察期内尚未继续的人也包含在“未继续”中，请结合运行错误判断。</div> : null}</li>)}</ol></section> : null}
      {tab === "retention" ? <section className="card"><h2>注册之后，还有多少家庭回来</h2><p className="muted small-text">回访包括访谈接通或阅读自己的成稿，按北京时间自然日统计。观察期未满的家庭不进入分母。</p><div className="admin-table-wrap"><table><thead><tr><th>时间</th><th>已观察家庭</th><th>回来使用</th><th>留存率</th></tr></thead><tbody>{data.retention.map(item => <tr key={item.day}><td>第 {item.day} 天</td><td>{item.eligible}</td><td>{item.retained}</td><td>{item.eligible ? percent(item.rate) : "待观察"}</td></tr>)}</tbody></table></div></section> : null}
      {tab === "runtime" ? <><section className="card"><h2>任务状态 · 全部历史</h2>{data.tasks.length ? data.tasks.map(item => <p key={item.status}>{({ queued: "等待处理", running: "正在处理", succeeded: "已完成", failed: "处理失败" } as Record<string, string>)[item.status] || item.status}：{item.count}</p>) : <p>尚无生成任务</p>}</section><section className="card"><h2>所选时段的错误</h2>{data.failures.length ? data.failures.map(item => <div key={`${item.stage}-${item.code}`}><p>{item.stage} · {item.count} 次</p><code>{item.code}</code></div>) : <p>尚未记录到业务错误事件</p>}</section><section className="card"><h2>费用</h2><p>供应商账单与费用归集尚未接入，此处暂无金额。</p></section></> : null}
      {tab === "accounts" ? <><section className="card"><h2>邀请新的家庭</h2><p>每个邀请码仅可注册一个独立家庭账户。</p><button className="primary" disabled={busy} onClick={invitation}>创建邀请码</button>{secret ? <div className="notice"><strong>{secret.label}</strong><p className="admin-secret">{secret.value}</p><button className="text-link" onClick={() => { void navigator.clipboard.writeText(secret.value).then(() => setMessage("已复制，请仅交给对应的使用者")).catch(() => setMessage("复制失败，请手动选择上方内容")); }}>复制</button><button className="text-link" onClick={() => setSecret(null)}>隐藏</button></div> : null}</section>
        <section className="card"><h2>家庭账户</h2><Status error={accounts.error} retry={accounts.reload} />{accounts.data?.items.map(item => <div className="admin-account" key={item.id}><div><strong>{item.username}</strong><p className="muted small-text">{item.role === "admin" ? "管理员" : "家庭账户"} · {item.enabled ? "正常" : "已停用"}</p></div><button className="text-link" disabled={busy} onClick={() => recovery(item.id)}>恢复密码</button></div>)}</section>
        <section className="card"><h2>邀请码记录</h2><Status error={invites.error} retry={invites.reload} />{invites.data?.items.length === 0 ? <p>尚未创建邀请码</p> : null}{invites.data?.items.map(item => <div className="admin-account" key={item.id}><div><p>{item.revoked ? "已撤销" : item.used ? "已使用" : new Date(item.expires_at).getTime() < new Date(data.as_of).getTime() ? "已过期" : "等待使用"}</p><p className="muted small-text">到期：{dateText(item.expires_at)}</p></div>{!item.used && !item.revoked ? <button className="text-link" disabled={busy} onClick={async () => { setBusy(true); try { await api(`/api/v1/admin/invitations/${item.id}`, { method: "DELETE" }); invites.reload(); } catch (cause) { setMessage(messageFor(cause)); } finally { setBusy(false); } }}>撤销</button> : null}</div>)}</section></> : null}
      <details className="card"><summary>统计口径</summary><p className="small-text">管理员账户已排除 {data.excluded_admin_accounts} 个。历史回忆录导入不计作新用户成书。样书阅读不计作阅读自己的成稿。分母为空显示暂无；匿名浏览器与真实人数可能存在差异。</p></details>
    </> : null}
  </div></Shell>;
}
