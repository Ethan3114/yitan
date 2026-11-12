import Link from "next/link";
import { BookCover } from "@/components/book-cover";
import { getShowcase } from "@/lib/showcase";

export const dynamic = "force-dynamic";
export default async function Books() {
  const books = await getShowcase();
  if (books.length === 0) return <main className="digital-book book-gallery"><nav className="reading-tools"><Link href="/welcome?step=3">‹ 返回引导</Link><span>忆谈 · 回忆录</span></nav><header><h1>您的故事，<br />从第一页开始。</h1><p>讲述过的经历会整理到这里，成为可以翻阅的回忆录。</p></header><Link className="primary" href="/profiles">开始讲述</Link></main>;
  return <main className="digital-book book-gallery"><nav className="reading-tools"><Link href="/welcome?step=3">‹ 返回引导</Link><span>忆谈 · 回忆录</span></nav><header><h1>翻开他们的书，<br />看见一段人生。</h1><p>从真实讲述中整理而来。</p></header><div className="showcase-shelf">{books.map(book => <Link href={`/books/${book.key}`} key={book.key}><BookCover title={book.title} name={book.author} /><h2>{book.author}</h2><p>{book.chapters.length} 章回忆</p><span className="text-link">翻开这本书 ›</span></Link>)}</div><Link className="secondary" href="/welcome?step=5">开始我的回忆录</Link></main>;
}
