import { notFound } from "next/navigation";
import { DigitalBook } from "@/components/digital-book";
import { getShowcase } from "@/lib/showcase";

export const dynamic = "force-dynamic";
export default async function Book({ params, searchParams }: { params: Promise<{ key: string }>; searchParams: Promise<{ view?: string; chapter?: string }> }) {
  const [route, query, books] = await Promise.all([params, searchParams, getShowcase()]);
  const book = books.find(item => item.key === route.key);
  if (!book) notFound();
  return <DigitalBook book={book} base={`/books/${book.key}`} back="/books" view={query.view} chapter={Number(query.chapter ?? 0)} />;
}
