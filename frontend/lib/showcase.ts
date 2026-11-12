import "server-only";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { cache } from "react";
import { z } from "zod";
import publicSamples from "@/content/public-samples.json";

const schema = z.array(z.object({ key: z.string(), title: z.string(), author: z.string(), characters: z.number(), chapters: z.array(z.object({ title: z.string(), paragraphs: z.array(z.string()).min(1) })).min(1) }));
export const getShowcase = cache(async () => {
  if (process.env.DEPLOYMENT_MODE === "cloud") {
    return schema.parse(publicSamples);
  }
  return schema.parse(JSON.parse(await readFile(resolve(process.cwd(), "../data/showcase-books.json"), "utf8")));
});
