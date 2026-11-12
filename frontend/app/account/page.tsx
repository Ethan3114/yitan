"use client";
import { useRouter } from "next/navigation";
import { BookOpen, Images, History, ShieldCheck, CircleHelp, UserRound, LogOut } from "lucide-react";
import { use, useState } from "react";
import { Shell, Status, MenuLink } from "@/components/shell";
import { profilesSchema } from "@/lib/models";
import { useResource } from "@/lib/use-resource";
import { api, messageFor } from "@/lib/api";
import { z } from "zod";
const accountSchema = z.object({ username: z.string(), role: z.string(), storage_mode: z.enum(["local", "cloud"]).optional() });
export default function Account({ searchParams: searchPromise }: { searchParams: Promise<{ profile?: string }> }) {
  const searchParams = use(searchPromise);
  const router = useRouter();
  const { data, error, unauthorized, reload } = useResource("/api/v1/profiles", profilesSchema);
  const account = useResource("/api/v1/account", accountSchema);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const selected = data?.items.find(item => item.id === searchParams.profile) ?? data?.items[0];
  async function logout() { setBusy(true); try { await api("/api/v1/auth/logout", { method: "POST" }); localStorage.removeItem("yitan.currentProfile"); router.replace("/"); } catch (cause) { setMessage(messageFor(cause)); setBusy(false); } }
  return <Shell title="我的" subtitle="认真保存每一份回忆" active="me" profileId={selected?.id}><Status error={error || message} unauthorized={unauthorized} retry={reload} loading={!data && !error} />{data ? <>
    <div className="account-head"><div className="profile-avatar"><UserRound size={30} /></div><div><h2>家庭回忆录</h2><p>正在记录 {data.items.length} 位家人的故事</p></div></div>
    <MenuLink icon={<BookOpen />} href="/profiles" title="我的回忆录" detail="翻开、切换或开始一本新书" />
    {account.data?.role === "admin" ? <MenuLink icon={<ShieldCheck />} href="/admin" title="管理面板" detail="查看用户转化、留存和运行情况" /> : null}
    {selected ? <><MenuLink icon={<UserRound />} href={`/profiles/${selected.id}/edit`} title="老人资料" detail={`${selected.elder_name} · 称呼、家乡与想聊的事`} /><MenuLink icon={<Images />} href={`/profiles/${selected.id}?tab=photos`} title="我的相册" detail="把老照片和它背后的故事留下" /><MenuLink icon={<History />} href={`/profiles/${selected.id}#history`} title="访谈记录" detail="听原声，找回讲过的往事" /></> : null}
    <section className="section"><p className="muted small-text" style={{ marginBottom: 14 }}>帮助与说明</p>
      <details className="card" style={{ marginBottom: 12 }}><summary><CircleHelp size={20} style={{ display: "inline", marginRight: 10 }} />使用帮助</summary><p className="small-text muted">开始访谈前，请由老人本人同意录音，并允许浏览器使用麦克风。访谈结束后点击“写成一段回忆”，整理期间可以离开页面。未完成的整理会显示原因和重试入口。</p></details>
      <MenuLink icon={<BookOpen />} href="/welcome" title="认识忆谈" detail="从一段讲述，到一本回忆录" />
      <details className="card"><summary><ShieldCheck size={20} style={{ display: "inline", marginRight: 10 }} />保存与隐私</summary><p className="small-text muted">{account.data?.storage_mode === "cloud" ? "资料保存在国内云端，按家庭独立授权。" : "资料按家庭分别保存。"}录音和照片加密保存。连续通话由国内豆包语音处理，录音转写使用国内通义听悟，文字整理使用国内阿里云千问。转写临时副本在处理后移除，家庭原声与文字继续保留。家人只能查看您明确展示的内容，原声需要单独授权。我们记录引导、注册、访谈和阅读等使用步骤，用于改善产品；统计不包含正文、录音内容和密码。请妥善保管账号、密码与邀请地址。</p></details>
    </section><button className="secondary section" type="button" onClick={logout} disabled={busy}><LogOut size={20} />{busy ? "正在退出…" : "退出登录"}</button>
  </> : null}</Shell>;
}
