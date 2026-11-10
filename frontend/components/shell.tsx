"use client";
import Link from "next/link";
import { ArrowLeft, BookOpen, UsersRound, UserRound, ChevronRight } from "lucide-react";
import { ReactNode } from "react";

export function Shell({ children, title, subtitle, back, profileId, active = "book", nav = true }: { children: ReactNode; title: string; subtitle?: string; back?: string; profileId?: string; active?: "book" | "family" | "me"; nav?: boolean }) {
  const base = profileId ? `/profiles/${profileId}` : "/profiles";
  return <div className="app-frame">
    <header className="page-header">
      {back ? <Link href={back} className="icon-button" aria-label="返回上一页"><ArrowLeft size={22} /></Link> : <Link href={profileId ? base : "/"} className="brand-mark" aria-label="忆谈，返回我的回忆录"><BookOpen size={25} /></Link>}
      <div className="header-copy"><h1>{title}</h1>{subtitle ? <p>{subtitle}</p> : null}</div>
      <span className="header-seal" aria-hidden="true">忆谈</span>
    </header>
    <main className={`page-content ${nav ? "with-nav" : ""}`}>{children}</main>
    {nav ? <nav className="bottom-nav" aria-label="主导航">
      <Link href={base} aria-current={active === "book" ? "page" : undefined}><BookOpen /><span>回忆录</span></Link>
      <Link href={profileId ? `${base}/family` : "/profiles?select=family"} aria-current={active === "family" ? "page" : undefined}><UsersRound /><span>家人</span></Link>
      <Link href={profileId ? `/account?profile=${profileId}` : "/account"} aria-current={active === "me" ? "page" : undefined}><UserRound /><span>我的</span></Link>
    </nav> : null}
  </div>;
}
export function Status({ error, unauthorized, loading, retry }: { error?: string; unauthorized?: boolean; loading?: boolean; retry?: () => void }) {
  if (loading) return <div className="loading-state" role="status"><div className="loading-line" /><p>正在翻开回忆…</p></div>;
  if (!error) return null;
  return <div className="notice error" role="alert"><p>{error}</p>{unauthorized ? <Link href="/login" className="text-link">重新登录</Link> : retry ? <button type="button" onClick={retry} className="text-link">重新读取</button> : null}</div>;
}
export function Empty({ icon, title, children }: { icon: ReactNode; title: string; children: ReactNode }) {
  return <section className="empty-state"><div className="empty-icon">{icon}</div><h2>{title}</h2><div>{children}</div></section>;
}
export function MenuLink({ href, icon, title, detail }: { href: string; icon: ReactNode; title: string; detail?: string }) {
  return <Link href={href} className="menu-row"><span className="menu-icon">{icon}</span><span className="menu-copy"><strong>{title}</strong>{detail ? <small>{detail}</small> : null}</span><ChevronRight size={20} /></Link>;
}
