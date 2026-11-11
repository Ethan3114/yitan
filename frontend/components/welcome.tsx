"use client";
import Link from "next/link";
import { BookCover } from "./book-cover";

const pages = [
  { label: "忆谈 · 口述回忆录", title: "把一生的故事，\n留在一本书里。", copy: "", scene: "" },
  { label: "序言 · 想起一件小事", title: "您小时候，\n家里最热闹的地方？", copy: "一张老照片、一顿家常饭，\n都能成为故事的第一页。", scene: "courtyard" },
  { label: "第一步 · 慢慢讲述", title: "像聊天一样，\n讲讲走过的日子。", copy: "忆谈陪您慢慢聊。\n想起什么，就从什么开始讲。", scene: "conversation" },
  { label: "第二步 · 整理成书", title: "讲过的日子，\n成为一本回忆录。", copy: "翻开他们的书，看看成书的样子。", scene: "conversation" },
  { label: "第三步 · 留给家人", title: "让熟悉的人，\n读懂您的这些年。", copy: "选几篇故事，邀请家人阅读。\n熟悉的声音，也可以一同留下。", scene: "family" },
  { label: "扉页 · 这是谁的书", title: "这本回忆录，\n想为谁留下？", copy: "讲自己的经历，或陪家人一起回忆。", scene: "" },
];

export function Welcome({ step }: { step: number }) {
  const content = pages[step];
  return <main className="book-introduction" data-design="book-second">
    <header className="book-introduction-nav"><Link href={step ? `/welcome?step=${step - 1}` : "/login"}>{step ? "‹ 返回" : "忆谈"}</Link><Link href="/login">已有回忆录</Link></header>
    <div className="guide-binding">
      <section key={step} className={`guide-leaf ${step === 0 ? "guide-cover" : ""} ${step === 5 ? "guide-title-page" : ""}`} aria-label={content.label} style={content.scene ? { backgroundImage: `linear-gradient(to bottom, #faf3e7 0%, #faf3e7ed 30%, #faf3e720 62%), url('/images/${content.scene}.png')` } : undefined}>
        {step === 0 ? <><p className="guide-imprint">口述回忆录</p><BookCover title="忆谈" /><h1>{content.title}</h1><p className="guide-signature">每个人的一生，都值得成书。</p></> : <div className="guide-copy"><p className="guide-label">{content.label}</p><h1>{content.title}</h1><p>{content.copy}</p>
          {step === 3 ? <div className="guide-book-links"><Link href="/books/li">李奶奶的故事 ›</Link><Link href="/books/yang">杨奶奶的故事 ›</Link></div> : null}
          {step === 5 ? <div className="guide-role-options">{[["self", "为自己记录"], ["mother", "为妈妈记录"], ["father", "为爸爸记录"], ["family", "为其他家人记录"]].map(([value, label]) => <Link key={value} href={`/login?for=${value}`}><span>{label}</span><span aria-hidden="true">›</span></Link>)}</div> : null}
        </div>}
        <span className="guide-folio">{String(step + 1).padStart(2, "0")}</span>
      </section>
    </div>
    <footer className="guide-controls">{step > 0 ? <Link href={`/welcome?step=${step - 1}`}>上一页</Link> : <span />}{step < 5 ? <Link className="primary" href={`/welcome?step=${step + 1}`}>{step === 0 ? "翻开这本书" : "下一页 ›"}</Link> : <Link href="/books">翻阅真实回忆录 ›</Link>}</footer>
  </main>;
}
