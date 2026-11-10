import { z } from "zod";
export const sharedBook = z.object({ title: z.string(), allow_audio: z.boolean(), chapters: z.array(z.object({ id: z.string(), title: z.string(), paragraphs: z.array(z.string()), call_id: z.string() })), photos: z.array(z.object({ id: z.string(), name: z.string() })) });
export const publication = sharedBook.extend({ id: z.string() });
export const comments = z.array(z.object({ id: z.string(), chapter_id: z.string(), name: z.string(), text: z.string(), mine: z.boolean(), created_at: z.string() }));
export const managementSchema = z.object({ publication: publication.nullable(), members: z.array(z.object({ id: z.string(), name: z.string(), allow_audio: z.boolean() })), invitations: z.array(z.object({ id: z.string(), expires_at: z.string() })), comments });
export const readingSchema = z.object({ name: z.string(), publication: publication.nullable(), comments });
export const previewSchema = z.object({ book: sharedBook, digest: z.string() });
export type SharedBook = z.infer<typeof sharedBook>;
