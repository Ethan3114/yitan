"use client";
import Link from "next/link";
import { useState } from "react";
import { ArrowLeft, BookOpen, Headphones, Heart, Mic } from "lucide-react";
import { BookCover } from "./book-cover";

const steps = [
  { title: "忆谈", lead: "把一生的故事，\n留给最亲的人", description: "和您聊聊往事，整理成一本\n可以读、可以听的回忆录。", action: "开始我的回忆录" },
  { title: "从一件小事，慢慢聊起", lead: "小时候的家，\n您还记得什么？", description: "一条回家的路，一顿家常饭。\n想起什么，就从什么开始讲。", action: "看看回忆怎样成书" },
  { title: "说过的往事，成为一页书", lead: "一段讲述，\n一篇属于您的回忆", description: "忆谈把讲述整理成章节。\n您可以阅读、修改，也可以重听原声。", action: "看看怎样留给家人" },
  { title: "让家人，读懂您的这些年", lead: "留下一本书，\n也留下熟悉的声音", description: "挑选愿意分享的章节，邀请家人阅读。\n是否分享原声，由您决定。", action: "开始记录我们的故事" },
];

export function Welcome({ step }: { step: number }) {
  const content = steps[step];
  const [opened, setOpened] = useState(false);
  return <main className={`onboarding welcome-${step}`}>
    <header className="onboarding-nav">{step > 0 ? <Link className="icon-button" href={`/welcome?step=${step - 1}`} aria-label="返回上一步"><ArrowLeft size={22} /></Link> : <span className="brand-word"><BookOpen size={22} />忆谈</span>}{step > 0 && step < 4 ? <><span className="muted small-text">{step} / 3</span><Link className="text-link" href="/welcome?step=4">直接开始</Link></> : <Link className="text-link" href="/login">已有回忆录</Link>}</header>
    {step < 4 ? <>
      <section className="onboarding-content">
        {step === 0 ? <><div className="book-stage"><BookCover title="一生的故事" /></div><h1 className="brand-title">{content.title}</h1></> : <><p className="eyebrow">{content.title}</p>{step === 1 ? <button type="button" className="memory-question" aria-expanded={opened} onClick={() => setOpened(value => !value)}><span className="memory-picture" aria-hidden="true" /><Mic size={30} /><span>{opened ? "那时，家里谁做的饭最香？" : "打开一张回忆卡"}</span></button> : <div className="book-stage"><BookCover title="我的故事" /></div>}</>}
        <h2>{content.lead}</h2><p className="onboarding-description">{content.description}</p>
        {step === 3 ? <div className="family-promises"><span><BookOpen size={20} />读回忆</span><span><Headphones size={20} />听原声</span><span><Heart size={20} />留句话</span></div> : null}
        {step === 2 ? <details className="preview-page"><summary>翻开书页，看看版式</summary><article><p className="eyebrow">书页版式预览</p><h3>第一章　童年的家</h3><p>这里将写下您亲口讲述的经历。地点、人物和细节，都来自您的回忆。</p><p>写好后可以查看、修改，也能回听当时的声音。</p></article></details> : null}
        {step === 3 ? <details className="preview-page"><summary>家人会看到什么？</summary><article><h3>您选择的故事</h3><p>家人接受邀请后，可以阅读您选好的章节、查看照片并留言。录音只有在您单独允许后才能收听。</p><p>当前请连接同一 Wi-Fi，并保持运行忆谈的电脑开启。</p></article></details> : null}
      </section>
      <footer className="onboarding-actions"><Link className="primary" href={`/welcome?step=${step + 1}`}>{content.action}</Link></footer>
    </> : <section className="onboarding-content role-selection"><BookOpen size={36} /><h1>这本回忆录，<br />想为谁留下？</h1><p className="onboarding-description">讲自己的经历，或陪家人一起回忆。</p><div className="role-options">{[["self", "为自己记录", "那些经历，值得亲口讲一讲"], ["mother", "为妈妈记录", "听听她年轻时的故事"], ["father", "为爸爸记录", "留下他一路走来的经历"], ["family", "为其他家人记录", "陪重要的人，慢慢回忆"]].map(([value, label, detail]) => <Link key={value} className="role-option" href={`/login?for=${value}`}><strong>{label}</strong><span>{detail}</span><span aria-hidden="true">→</span></Link>)}</div></section>}
  </main>;
}
