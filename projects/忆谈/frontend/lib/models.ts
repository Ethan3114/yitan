import { z } from "zod";

export const paragraphSchema = z.object({ id: z.string(), text: z.string(), source_quote: z.string(), source_quotes: z.array(z.object({ line: z.number(), quote: z.string() })).default([]) });
export const chapterSchema = z.object({
  id: z.string(), title: z.string(), paragraphs: z.array(paragraphSchema),
  facts: z.array(z.object({ id: z.string(), topic: z.string(), detail: z.string(), status: z.string() })),
  memories: z.array(z.object({ kind: z.string(), name: z.string(), detail: z.string() })),
});
export const taskSchema = z.object({ id: z.string(), call_id: z.string(), status: z.enum(["queued", "running", "succeeded", "failed"]), step: z.string(), error_code: z.string(), created_at: z.string(), updated_at: z.string() });
export const callSchema = z.object({ id: z.string(), status: z.string(), failure_code: z.string(), texts: z.record(z.string(), z.string()), chapters: z.array(chapterSchema), created_at: z.string(), audio_kinds: z.array(z.string()), task: taskSchema.nullable() });
export const profileSchema = z.object({
  id: z.string(), elder_name: z.string(), book_title: z.string(), birth_year: z.number().nullable(), hometown: z.string(), family_names: z.string(), topics_want: z.string(), topics_avoid: z.string(), calls: z.array(callSchema),
});
export const profilesSchema = z.object({ items: z.array(z.object({ id: z.string(), elder_name: z.string(), hometown: z.string() })) });
export const photosSchema = z.object({ items: z.array(z.object({ id: z.string(), name: z.string(), created_at: z.string(), url: z.string() })) });
export const meSchema = z.object({ owner_id: z.string(), voice_ready: z.boolean(), writing_ready: z.boolean() });
export const readerSchema = z.object({ profile_id: z.string(), elder_name: z.string(), call_id: z.string(), audio_kinds: z.array(z.string()), chapter: chapterSchema });
export type Profile = z.infer<typeof profileSchema>;
export type Call = z.infer<typeof callSchema>;
export type Chapter = z.infer<typeof chapterSchema>;
export type Photo = z.infer<typeof photosSchema>["items"][number];
export type DraftTask = z.infer<typeof taskSchema>;
export function dateLabel(date: string) {
  return new Intl.DateTimeFormat("zh-CN", { month: "long", day: "numeric" }).format(new Date(date.endsWith("Z") || date.includes("+") ? date : `${date}Z`));
}
