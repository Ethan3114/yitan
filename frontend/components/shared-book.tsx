import Image from "next/image";
import { SharedBook } from "@/lib/family";
import { BookCover } from "./book-cover";
export function SharedBookPreview({ book, family = false }: { book: SharedBook; family?: boolean }) {
  return <div className="reader"><div className="book-stage"><BookCover title={book.title} /></div><p className="eyebrow">留给家人的故事</p><h2>{book.title}</h2>
    {book.chapters.map(chapter => <article className="section" key={chapter.id}><h3>{chapter.title}</h3><div className="reading-body">{chapter.paragraphs.map((text, i) => <p className="reading-paragraph" key={i}>{text}</p>)}</div>
      {book.allow_audio ? <details><summary>听这次访谈的录音</summary><p className="small-text muted">录音包含这次访谈的其他内容。新访谈播放双方对话，旧访谈保留麦克风录音。</p><audio className="audio" controls preload="none" src={family ? `/api/v1/family/audio/${chapter.call_id}` : `/api/v1/calls/${chapter.call_id}/audio/playback`} /></details> : null}</article>)}
    <div className="photo-grid section">{book.photos.map(photo => <Image key={photo.id} src={family ? `/api/v1/family/photos/${photo.id}` : `/api/v1/photos/${photo.id}`} alt={photo.name} width={300} height={300} unoptimized />)}</div>
  </div>;
}
