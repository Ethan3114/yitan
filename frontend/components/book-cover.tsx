export function BookCover({ title, name, small = false }: { title: string; name?: string; small?: boolean }) {
  return <div className={`book-cover ${small ? "small" : ""}`} aria-label={name ? `${title}，${name}的回忆录` : `${title}，回忆录`}>
    <span className="book-pages" aria-hidden="true" />
    <div className="book-cover-inner"><h2>{title}</h2><p>{name ? `${name} 口述` : "口述回忆录"}</p><span className="cover-rule" /><span className="cover-publisher">忆谈</span></div>
  </div>;
}
