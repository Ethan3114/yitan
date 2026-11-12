"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";
import { Activity, ArrowDownRight, ArrowUpRight, BarChart3, BookOpen, ChevronRight, Clock3, LayoutDashboard, LogIn, RefreshCw, Settings2, Users, Waypoints } from "lucide-react";
import { z } from "zod";
import { Status } from "@/components/shell";
import { useResource } from "@/lib/use-resource";
import { api, messageFor } from "@/lib/api";
import { useHydrated } from "@/lib/use-hydrated";
import "./panel.css";

const trendSchema = z.object({ date: z.string(), visitors: z.number(), sessions: z.number(), started_interviews: z.number(), completed_interviews: z.number(), completed_memoirs: z.number(), returned_visitors: z.number() });
const metricsSchema = z.object({
  as_of: z.string(), latest_event_at: z.string().nullable(), observing_cohorts: z.number(), excluded_admin_accounts: z.number(),
  funnel: z.array(z.object({ event: z.string(), stage: z.string(), label: z.string(), count: z.number(), previous: z.number(), rate: z.number().nullable(), not_completed: z.number() })),
  retention: z.array(z.object({ day: z.number(), eligible: z.number(), retained: z.number(), rate: z.number().nullable() })),
  activity: z.record(z.string(), z.number()),
  failures: z.array(z.object({ stage: z.string(), code: z.string(), count: z.number() })),
  tasks: z.array(z.object({ status: z.string(), count: z.number() })),
  journey: z.object({
    summary: z.object({ visitors: z.number(), sessions: z.number(), page_views: z.number(), new_registrations: z.number(), started_interviews: z.number(), completed_interviews: z.number(), completed_memoirs: z.number(), returned_visitors: z.number(), active_sessions: z.number(), exited_sessions: z.number(), experience_rate: z.number().nullable(), bounce_rate: z.number().nullable() }),
    trend: z.array(trendSchema), funnel: z.array(z.object({ id: z.string(), label: z.string(), visitors: z.number(), rate: z.number().nullable() })),
    pages: z.array(z.object({ id: z.string(), label: z.string(), visitors: z.number(), views: z.number(), entries: z.number(), exits: z.number(), active_sessions: z.number(), exit_rate: z.number().nullable(), avg_duration_seconds: z.number().nullable() })),
    definitions: z.array(z.string()), collection_started_at: z.string().nullable(), page_tracking_started_at: z.string().nullable(),
    excluded: z.object({ admin_accounts: z.number(), test_accounts: z.number(), admin_events: z.number(), test_events: z.number() }), filters: z.object({ include_admin: z.boolean(), include_test: z.boolean() }),
  }),
});
type Journey = z.infer<typeof metricsSchema>["journey"];
type Tab = "overview" | "funnel" | "pages" | "retention" | "runtime" | "accounts";
const accountsSchema = z.object({ items: z.array(z.object({ id: z.string(), username: z.string(), role: z.string(), enabled: z.boolean(), created_at: z.string() })) });
const invitesSchema = z.object({ items: z.array(z.object({ id: z.string(), created_at: z.string(), expires_at: z.string(), used: z.boolean(), revoked: z.boolean() })) });
const NAV = [
  { id: "overview" as const, label: "体验总览", detail: "进入与继续", icon: LayoutDashboard },
  { id: "funnel" as const, label: "体验转化", detail: "从翻书到成稿", icon: Waypoints },
  { id: "pages" as const, label: "页面流失", detail: "用户停在哪里", icon: ArrowDownRight },
  { id: "retention" as const, label: "回访留存", detail: "有多少人回来", icon: Users },
  { id: "runtime" as const, label: "运行监控", detail: "任务与错误", icon: Activity },
  { id: "accounts" as const, label: "账户与邀请", detail: "管理体验资格", icon: Settings2 },
];
const percent = (value: number | null) => value === null ? "暂无" : `${(value * 100).toFixed(1)}%`;
const dateText = (value: string) => new Date(value.endsWith("Z") || /[+-]\d\d:\d\d$/.test(value) ? value : value + "Z").toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false });
const durationText = (seconds: number | null) => seconds === null ? "未采集" : seconds < 60 ? `${Math.round(seconds)} 秒` : `${Math.floor(seconds / 60)} 分 ${Math.round(seconds % 60)} 秒`;
const beijingDay = (offset = 0) => new Date(Date.now() + 8 * 3600000 + offset * 86400000).toISOString().slice(0, 10);
const hasPageActivity = (page: Journey["pages"][number]) => page.visitors > 0 || page.exits > 0 || page.active_sessions > 0;

function EmptyChart({ children }: { children: React.ReactNode }) {
  return <div className="admin-empty"><BarChart3 size={30} /><strong>等待真实访问</strong><p>{children}</p></div>;
}

function TrendChart({ points }: { points: Journey["trend"] }) {
  if (!points.some(point => point.visitors || point.started_interviews)) return <EmptyChart>所选日期尚无访问记录，收到访问后会显示每日人数。</EmptyChart>;
  const width = 760, height = 250, left = 36, right = 16, top = 18, bottom = 35;
  const graphWidth = width - left - right, graphHeight = height - top - bottom;
  const maximum = Math.max(1, ...points.map(point => Math.max(point.visitors, point.started_interviews)));
  const ceiling = maximum <= 4 ? 4 : Math.ceil(maximum / 4) * 4;
  const x = (index: number) => left + (points.length === 1 ? graphWidth / 2 : index / (points.length - 1) * graphWidth);
  const y = (value: number) => top + graphHeight - value / ceiling * graphHeight;
  const visitorLine = points.map((point, index) => `${x(index)},${y(point.visitors)}`).join(" ");
  const experienceLine = points.map((point, index) => `${x(index)},${y(point.started_interviews)}`).join(" ");
  const area = `${x(0)},${y(0)} ${visitorLine} ${x(points.length - 1)},${y(0)}`;
  const tickStep = Math.max(1, Math.ceil((points.length - 1) / 6));
  return <><div className="admin-chart-legend"><span><i />到访人数</span><span><i className="is-gold" />开始访谈人数</span></div><div className="admin-chart" data-admin-chart="trend"><svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="每日到访与开始访谈人数趋势">
    <defs><linearGradient id="visitor-area" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stopColor="#46765d" stopOpacity=".17" /><stop offset="100%" stopColor="#46765d" stopOpacity="0" /></linearGradient></defs>
    {[0, 1, 2, 3, 4].map(index => <g key={index}><line x1={left} x2={width - right} y1={y(ceiling * index / 4)} y2={y(ceiling * index / 4)} stroke="#e9eeea" strokeDasharray="4 4" /><text x={left - 10} y={y(ceiling * index / 4) + 4} textAnchor="end">{ceiling * index / 4}</text></g>)}
    <polygon points={area} fill="url(#visitor-area)" /><polyline points={visitorLine} fill="none" stroke="#38644c" strokeWidth="2.5" strokeLinejoin="round" /><polyline points={experienceLine} fill="none" stroke="#bf9144" strokeWidth="2.5" strokeLinejoin="round" />
    {points.map((point, index) => <g key={point.date}><circle cx={x(index)} cy={y(point.visitors)} r="3.5" fill="#38644c"><title>{point.date}：到访 {point.visitors} 人，开始访谈 {point.started_interviews} 人</title></circle>{index % tickStep === 0 || index === points.length - 1 ? <text x={x(index)} y={height - 10} textAnchor="middle">{point.date.slice(5).replace("-", "/")}</text> : null}</g>)}
  </svg></div><details className="admin-chart-data"><summary>查看每日明细</summary><div className="admin-table-wrap"><table><thead><tr><th>日期</th><th>到访</th><th>开始访谈</th><th>完成访谈</th><th>完成成稿</th><th>再次到访</th></tr></thead><tbody>{points.map(point => <tr key={point.date}><td>{point.date}</td><td>{point.visitors}</td><td>{point.started_interviews}</td><td>{point.completed_interviews}</td><td>{point.completed_memoirs}</td><td>{point.returned_visitors}</td></tr>)}</tbody></table></div></details></>;
}

function ArrivalChart({ journey, compact = false }: { journey: Journey; compact?: boolean }) {
  if (!journey.summary.visitors) return <EmptyChart>收到访客后，展示引导、注册、访谈和成稿的实际到达人数。</EmptyChart>;
  const items = compact ? journey.funnel.filter(item => ["visited", "registered", "interview", "recorded", "memoir"].includes(item.id)) : journey.funnel;
  const visible = items.length ? items : journey.funnel;
  return <ol className="admin-arrivals" data-admin-chart="arrivals">{visible.map((item, index) => <li key={item.id}><div className="admin-arrival-copy"><span><i>{String(index + 1).padStart(2, "0")}</i>{item.label}</span><strong>{item.visitors}<small>人</small></strong></div><div className="admin-arrival-track"><span style={{ width: `${Math.min(100, (item.rate || 0) * 100)}%`, backgroundColor: `hsl(${145 - index * 3} 19% ${35 + index * 4}%)` }} /></div><span className="admin-arrival-rate">占全部到访 {percent(item.rate)}</span></li>)}</ol>;
}

function ExitChart({ pages }: { pages: Journey["pages"] }) {
  const rows = [...pages].filter(hasPageActivity).sort((a, b) => b.exits - a.exits || b.views - a.views).slice(0, 6);
  if (!rows.length) return <EmptyChart>页面访问开始采集后，显示每个页面的结束会话数。</EmptyChart>;
  const maximum = Math.max(1, ...rows.map(row => row.exits));
  return <div className="admin-exits" data-admin-chart="exits">{rows.map(row => <div className="admin-exit-row" key={row.id}><div><span>{row.label}</span><strong>{row.exits}<small>次结束会话</small></strong></div><div className="admin-exit-track"><span style={{ width: `${row.exits / maximum * 100}%` }} /></div><p>{row.visitors} 人访问 · {row.active_sessions} 次会话仍在访问</p></div>)}</div>;
}

function Kpi({ label, value, note, icon: Icon, accent = false }: { label: string; value: string | number; note: string; icon: typeof Users; accent?: boolean }) {
  return <article className={`admin-kpi ${accent ? "admin-kpi-accent" : ""}`}><div className="admin-kpi-label"><span>{label}</span><Icon size={19} /></div><strong>{value}</strong><p>{note}</p></article>;
}

export default function Admin() {
  const hydrated = useHydrated();
  const [tab, setTab] = useState<Tab>("overview");
  const [preset, setPreset] = useState("7");
  const [dates, setDates] = useState({ start: beijingDay(-6), end: beijingDay() });
  const [draftDates, setDraftDates] = useState(dates);
  const [includeAdmin, setIncludeAdmin] = useState(false);
  const [includeTest, setIncludeTest] = useState(false);
  const query = new URLSearchParams({ ...dates, include_admin: String(includeAdmin), include_test: String(includeTest) });
  const metrics = useResource(`/api/v1/admin/metrics?${query}`, metricsSchema);
  const accounts = useResource("/api/v1/admin/accounts", accountsSchema);
  const invites = useResource("/api/v1/admin/invitations", invitesSchema);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [secret, setSecret] = useState<{ label: string; value: string } | null>(null);
  const [selectedStage, setSelectedStage] = useState<number | null>(null);
  const data = metrics.data;
  const journey = data?.journey;
  const continuedSessions = journey ? journey.summary.sessions - journey.summary.active_sessions - journey.summary.exited_sessions : 0;
  const current = NAV.find(item => item.id === tab)!;
  const needsAdmin = metrics.unauthorized || metrics.error.includes("仅管理员");
  const copied = message.startsWith("已复制");
  function filter(event: FormEvent<HTMLFormElement>) { event.preventDefault(); setDates(draftDates); setPreset("custom"); setSelectedStage(null); }
  function range(days: string) { const next = { start: beijingDay(1 - Number(days)), end: beijingDay() }; setDates(next); setDraftDates(next); setPreset(days); setSelectedStage(null); }
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
  function refresh() { metrics.reload(); if (tab === "accounts") { accounts.reload(); invites.reload(); } }

  return <div className="admin-workspace" data-layout="desktop-admin">
    <aside className="admin-sidebar"><Link href="/admin" className="admin-brand"><span><BookOpen size={25} /></span><div><strong>忆谈</strong><small>产品管理中心</small></div></Link><p className="admin-sidebar-label">产品运营</p><nav aria-label="后台页面">{NAV.map(item => <button type="button" key={item.id} aria-current={tab === item.id ? "page" : undefined} disabled={!hydrated} onClick={() => setTab(item.id)}><item.icon size={20} /><span>{item.label}<small>{item.detail}</small></span>{tab === item.id ? <ChevronRight size={15} /> : null}</button>)}</nav><div className="admin-sidebar-bottom"><p><span />国内云端 · 家庭独立授权</p><Link href="/account"><LogIn size={17} />返回产品</Link></div></aside>
    <div className="admin-main"><header className="admin-topbar"><div><span>忆谈管理</span><ChevronRight size={14} /><strong>{current.label}</strong></div><span className="admin-timezone"><Clock3 size={14} />北京时间</span></header>
      <main className="admin-content"><div className="admin-title-row"><div><p className="admin-eyebrow">YITAN / PRODUCT ANALYTICS</p><h1>{current.label}</h1><p>{({ overview: "看见谁来过、在哪里停下，以及有多少人真正开始讲述。", funnel: "观察每一步的实际到达人数，找到继续体验的机会。", pages: "查看访客结束体验前，最后停留在哪个页面。", retention: "了解家庭注册之后，是否再次回来讲述和阅读。", runtime: "关注访谈和成稿任务的状态，及时发现使用障碍。", accounts: "发放体验资格，管理家庭账户与一次性恢复码。" })[tab]}</p></div><button type="button" className="admin-button" onClick={refresh} disabled={!hydrated}><RefreshCw size={16} />刷新数据</button></div>
      <section className="admin-controls" aria-label="统计筛选"><div className="admin-range-buttons">{[["7", "近 7 天"], ["30", "近 30 天"], ["90", "近 90 天"]].map(([value, label]) => <button key={value} type="button" aria-pressed={preset === value} disabled={!hydrated} onClick={() => range(value)}>{label}</button>)}</div><form method="get" className="admin-filter" onSubmit={filter}><label><span>开始日期</span><input aria-label="开始日期" type="date" name="start" value={draftDates.start} max={draftDates.end || beijingDay()} required onChange={event => setDraftDates({ ...draftDates, start: event.target.value })} /></label><span aria-hidden="true">—</span><label><span>结束日期</span><input aria-label="结束日期" type="date" name="end" value={draftDates.end} min={draftDates.start} max={beijingDay()} required onChange={event => setDraftDates({ ...draftDates, end: event.target.value })} /></label><button type="submit" className="admin-button" disabled={!hydrated}>应用</button></form></section>
      <details className="admin-diagnostics"><summary><Settings2 size={14} />统计范围与诊断</summary><div><label><input type="checkbox" checked={includeAdmin} onChange={event => setIncludeAdmin(event.target.checked)} />包含管理员的产品体验</label><label><input type="checkbox" checked={includeTest} onChange={event => setIncludeTest(event.target.checked)} />包含验证账户</label><p>默认排除管理员和验证账户。使用管理账号体验产品时，可开启诊断查看对应记录；管理面板本身不计入访问。</p></div></details>
      <Status loading={!data && !metrics.error} error={metrics.error || (copied ? "" : message)} unauthorized={metrics.unauthorized} retry={metrics.reload} />
      {needsAdmin ? <p className="admin-access-help">请使用管理员账户查看此面板。<Link href="/login?next=/admin">登录管理账户 <ChevronRight size={14} /></Link></p> : null}
      {copied ? <p className="admin-info" role="status">{message}</p> : null}
      {data && journey ? <><div className="admin-data-caption"><span><i />{includeAdmin || includeTest ? "诊断统计" : "实际访客统计"} · {dates.start} 至 {dates.end}</span><span>更新于 {dateText(data.as_of)}</span></div>
        {!journey.summary.visitors ? <div className="admin-info"><Users size={18} /><p>所选日期暂无符合统计范围的访问。管理员体验默认排除；匿名新访客进入引导后会自动计数。</p></div> : null}
        {!journey.page_tracking_started_at ? <div className="admin-info"><Clock3 size={18} /><p>页面访问采集尚未收到记录。历史阶段事件继续保留，页面停留与退出从本次访问采集开始统计。</p></div> : null}
        {tab === "overview" ? <><section className="admin-kpis" aria-label="体验核心人数"><Kpi label="过来体验" value={journey.summary.visitors} note="匿名浏览器与家庭账户合并去重" icon={Users} accent /><Kpi label="开始讲述" value={journey.summary.started_interviews} note={`到访后接通访谈 · ${percent(journey.summary.experience_rate)}`} icon={Activity} /><Kpi label="完整留下一段" value={journey.summary.completed_interviews} note="已保存至少一分钟的真实访谈" icon={BookOpen} /><Kpi label="再次回来" value={journey.summary.returned_visitors} note="曾访问后，又发起新的访问会话" icon={ArrowUpRight} /></section>
          <div className="admin-grid admin-grid-main"><section className="admin-card"><div className="admin-card-heading"><div><h2>体验人数趋势</h2><p>到访与开始访谈，按天查看</p></div><span className="admin-unit">人数 / 天</span></div><TrendChart points={journey.trend} /></section><section className="admin-card admin-session-card"><div className="admin-card-heading"><div><h2>访问之后发生了什么</h2><p>会话与业务行为分别统计</p></div></div><div className="admin-session-total"><strong>{journey.summary.sessions}</strong><span>访问会话</span></div><dl className="admin-session-list"><div><dt><i className="dot-live" />仍在访问</dt><dd>{journey.summary.active_sessions}</dd></div><div><dt><i />已结束访问</dt><dd>{journey.summary.exited_sessions}</dd></div>{continuedSessions > 0 ? <div><dt><i />日期范围后仍有访问</dt><dd>{continuedSessions}</dd></div> : null}<div><dt><i className="dot-gold" />仅访问一页后离开</dt><dd>{percent(journey.summary.bounce_rate)}</dd></div></dl><div className="admin-session-footer"><span>新注册家庭 <strong>{journey.summary.new_registrations}</strong></span><span>完成成稿 <strong>{journey.summary.completed_memoirs}</strong></span></div><p className="admin-note">连续 30 分钟没有活动，计作会话结束。仍在访问的会话单独展示。{continuedSessions > 0 ? "日期范围之后继续访问的会话，不计入本区间退出。" : ""}</p></section></div>
          <div className="admin-grid"><section className="admin-card"><div className="admin-card-heading"><div><h2>从到访，到留下故事</h2><p>每一步独立统计到达人数</p></div><button className="admin-inline-button" onClick={() => setTab("funnel")}>查看全部 <ChevronRight size={14} /></button></div><ArrivalChart journey={journey} compact /></section><section className="admin-card"><div className="admin-card-heading"><div><h2>用户在哪里离开</h2><p>已结束会话的最后停留页</p></div><button className="admin-inline-button" onClick={() => setTab("pages")}>页面明细 <ChevronRight size={14} /></button></div><ExitChart pages={journey.pages} /></section></div>
        </> : null}
        {tab === "funnel" ? <><div className="admin-grid admin-grid-main"><section className="admin-card"><div className="admin-card-heading"><div><h2>体验阶段到达</h2><p>允许从登录页、样书和历史入口进入；不会把跳过引导的人判作流失</p></div></div><ArrivalChart journey={journey} /></section><section className="admin-card"><div className="admin-card-heading"><div><h2>继续体验</h2><p>真正接通一次访谈</p></div></div><div className="admin-conversion-ring" style={{ background: `conic-gradient(#38644c ${(journey.summary.experience_rate || 0) * 360}deg, #edf1ed 0)` }}><div><strong>{percent(journey.summary.experience_rate)}</strong><span>开始访谈占比</span></div></div><p className="admin-conversion-copy"><strong>{journey.summary.started_interviews}</strong> / {journey.summary.visitors} 位到访者开始访谈</p><p className="admin-note">阶段人数可包含已注册家庭的回访。各阶段分别展示占全部到访人数的比例。</p></section></div>
          <section className="admin-card"><div className="admin-card-heading"><div><h2>完整引导顺序</h2><p>仅查看首次从品牌页进入、按顺序完成的访客 · 七天观察窗口</p></div><span className="admin-badge">{data.observing_cohorts} 位仍在观察</span></div><ol className="admin-sequence">{data.funnel.map((item, index) => <li key={`${item.event}-${item.stage}`}><button type="button" aria-expanded={selectedStage === index} onClick={() => setSelectedStage(selectedStage === index ? null : index)}><span><i>{String(index + 1).padStart(2, "0")}</i>{item.label}</span><strong>{item.count}<small>人</small></strong><span>{percent(item.rate)}</span></button><div className="admin-sequence-bar"><span style={{ width: `${data.funnel[0]?.count ? item.count / data.funnel[0].count * 100 : 0}%` }} /></div>{selectedStage === index ? <p className="admin-note">上一步 {item.previous} 人，本步 {item.count} 人，暂未到达本步 {item.not_completed} 人。观察期内的人数尚未构成最终流失。</p> : null}</li>)}</ol></section></> : null}
        {tab === "pages" ? <><div className="admin-grid"><section className="admin-card"><div className="admin-card-heading"><div><h2>结束体验前的最后一页</h2><p>按结束会话数量排序</p></div></div><ExitChart pages={journey.pages} /></section><section className="admin-card admin-exit-explainer"><ArrowDownRight size={28} /><h2>翻过一页，继续阅读也会离开当前页</h2><p>“结束会话”以连续 30 分钟没有活动为准，最后停留的页面才计入退出。翻到下一页、打开样书、进入登录页面，都属于继续体验。</p><dl><div><dt>已结束会话</dt><dd>{journey.summary.exited_sessions}</dd></div><div><dt>仍在访问</dt><dd>{journey.summary.active_sessions}</dd></div><div><dt>页面浏览次数</dt><dd>{journey.summary.page_views}</dd></div></dl></section></div><section className="admin-card"><div className="admin-card-heading"><div><h2>所有页面明细</h2><p>页面访问采集开始于 {journey.page_tracking_started_at ? dateText(journey.page_tracking_started_at) : "尚未收到记录"}</p></div></div><div className="admin-table-wrap"><table><thead><tr><th>页面</th><th>到访人数</th><th>浏览次数</th><th>作为入口</th><th>最后停留并退出</th><th>退出比例</th><th>仍在访问</th><th>平均可见停留</th></tr></thead><tbody>{journey.pages.filter(hasPageActivity).map(page => <tr key={page.id}><td className="admin-page-name">{page.label}</td><td>{page.visitors}</td><td>{page.views}</td><td>{page.entries}</td><td>{page.exits}</td><td>{percent(page.exit_rate)}</td><td>{page.active_sessions}</td><td>{durationText(page.avg_duration_seconds)}</td></tr>)}</tbody></table>{!journey.pages.some(hasPageActivity) ? <p className="admin-table-empty">所选日期暂无页面访问记录</p> : null}</div></section></> : null}
        {tab === "retention" ? <><section className="admin-kpis"><Kpi label="再次到访" value={journey.summary.returned_visitors} note="曾访问后，又发起新的访问会话" icon={Users} />{data.retention.map(item => <Kpi key={item.day} label={`第 ${item.day} 天家庭留存`} value={item.eligible ? percent(item.rate) : "待观察"} note={`${item.retained} / ${item.eligible} 个已满观察时间的家庭`} icon={Clock3} />)}</section><section className="admin-card"><div className="admin-card-heading"><div><h2>注册家庭留存</h2><p>按北京时间自然日，观察注册后第 1、7、30 天是否回来访谈或阅读</p></div></div><div className="admin-retention-bars">{data.retention.map(item => <div key={item.day}><div><span>第 {item.day} 天</span><strong>{item.eligible ? percent(item.rate) : "待观察"}</strong></div><div className="admin-arrival-track"><span style={{ width: `${(item.rate || 0) * 100}%` }} /></div><p>{item.eligible ? `${item.eligible} 个家庭已满观察时间，${item.retained} 个回来使用` : "注册家庭尚未达到本观察日期"}</p></div>)}</div><p className="admin-note">尚未满观察时间的家庭不进入分母。翻阅公开样书不计入家庭回访；阅读自己的回忆录、家人阅读或接通访谈计入。</p></section><section className="admin-card"><div className="admin-card-heading"><div><h2>每日访问与体验</h2><p>日期范围内的真实到访人数</p></div></div><TrendChart points={journey.trend} /></section></> : null}
        {tab === "runtime" ? <><section className="admin-card"><div className="admin-card-heading"><div><h2>成稿任务</h2><p>全部历史任务状态</p></div></div><div className="admin-task-grid">{["queued", "running", "succeeded", "failed"].map(status => <div key={status} data-status={status}><span>{({ queued: "等待处理", running: "正在处理", succeeded: "已完成", failed: "处理失败" } as Record<string, string>)[status]}</span><strong>{data.tasks.find(item => item.status === status)?.count || 0}</strong></div>)}</div></section><div className="admin-grid"><section className="admin-card"><div className="admin-card-heading"><div><h2>所选时段的错误</h2><p>仅展示阶段和错误代码，保护访谈内容</p></div></div>{data.failures.length ? <div className="admin-table-wrap"><table><thead><tr><th>阶段</th><th>错误代码</th><th>次数</th></tr></thead><tbody>{data.failures.map(item => <tr key={`${item.stage}-${item.code}`}><td>{item.stage}</td><td><code>{item.code}</code></td><td>{item.count}</td></tr>)}</tbody></table></div> : <div className="admin-empty"><Activity size={28} /><strong>尚未记录到业务错误</strong><p>失败任务请结合访谈记录查看具体处理状态。</p></div>}</section><section className="admin-card"><div className="admin-card-heading"><div><h2>费用记录</h2><p>供应商账单与资源使用</p></div></div><p className="admin-note">金额尚未接入供应商账单。当前页面展示真实任务与事件数量，模型费用请以阿里云、豆包账单为准。</p></section></div></> : null}
        {tab === "accounts" ? <><section className="admin-card"><div className="admin-card-heading"><div><h2>邀请新的家庭</h2><p>每个邀请码仅可注册一个独立家庭账户</p></div><button type="button" className="admin-button admin-button-primary" disabled={busy || !hydrated} onClick={invitation}>创建邀请码</button></div>{secret ? <div className="admin-secret-box"><strong>{secret.label}</strong><p className="admin-secret">{secret.value}</p><div><button className="admin-button" onClick={() => { void navigator.clipboard.writeText(secret.value).then(() => setMessage("已复制，请仅交给对应的使用者")).catch(() => setMessage("复制失败，请手动选择上方内容")); }}>复制</button><button className="admin-inline-button" onClick={() => setSecret(null)}>隐藏</button></div></div> : null}</section><section className="admin-card"><div className="admin-card-heading"><div><h2>家庭账户</h2><p>账户名称与访问角色</p></div></div><Status error={accounts.error} retry={accounts.reload} /><div className="admin-table-wrap"><table><thead><tr><th>账户</th><th>角色</th><th>状态</th><th>注册时间</th><th>操作</th></tr></thead><tbody>{accounts.data?.items.map(item => <tr key={item.id}><td className="admin-page-name">{item.username}</td><td>{item.role === "admin" ? "管理员" : "家庭账户"}</td><td><span className={`admin-badge ${item.enabled ? "" : "admin-badge-warning"}`}>{item.enabled ? "正常" : "已停用"}</span></td><td>{dateText(item.created_at)}</td><td><button className="admin-inline-button" disabled={busy} onClick={() => recovery(item.id)}>恢复密码</button></td></tr>)}</tbody></table></div></section><section className="admin-card"><div className="admin-card-heading"><div><h2>邀请码记录</h2><p>邀请码仅在创建时显示，请妥善分发</p></div></div><Status error={invites.error} retry={invites.reload} /><div className="admin-table-wrap"><table><thead><tr><th>状态</th><th>创建时间</th><th>到期时间</th><th>操作</th></tr></thead><tbody>{invites.data?.items.map(item => <tr key={item.id}><td>{item.revoked ? "已撤销" : item.used ? "已使用" : new Date(item.expires_at).getTime() < new Date(data.as_of).getTime() ? "已过期" : "等待使用"}</td><td>{dateText(item.created_at)}</td><td>{dateText(item.expires_at)}</td><td>{!item.used && !item.revoked ? <button className="admin-inline-button" disabled={busy} onClick={async () => { setBusy(true); try { await api(`/api/v1/admin/invitations/${item.id}`, { method: "DELETE" }); invites.reload(); } catch (cause) { setMessage(messageFor(cause)); } finally { setBusy(false); } }}>撤销</button> : "—"}</td></tr>)}</tbody></table>{invites.data?.items.length === 0 ? <p className="admin-table-empty">尚未创建邀请码</p> : null}</div></section></> : null}
        <details className="admin-definitions"><summary>统计口径与数据范围</summary><ul>{journey.definitions.map(definition => <li key={definition}>{definition}</li>)}<li>页面按功能分类统计。样书的列表、封面、目录和正文合并为样书；访谈准备与通话合并为访谈；引导的六页分别记录。</li></ul><p>管理员账户 {journey.excluded.admin_accounts} 个，验证账户 {journey.excluded.test_accounts} 个。当前时段识别到管理员事件 {journey.excluded.admin_events} 条（{includeAdmin ? "已包含" : "已排除"}），验证事件 {journey.excluded.test_events} 条（{includeTest ? "已包含" : "已排除"}）。</p><p>首次事件：{journey.collection_started_at ? dateText(journey.collection_started_at) : "暂无"}；页面访问开始：{journey.page_tracking_started_at ? dateText(journey.page_tracking_started_at) : "暂无"}。匿名浏览器与真实人数存在差异，注册后按家庭合并。</p></details>
      </> : null}
      </main><footer className="admin-footer"><span>忆谈 · 让每一段经历被认真留下</span><span>仅管理员可见</span></footer>
    </div>
  </div>;
}
