import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

export const dynamic = "force-static";

export async function GET() {
  const [script, license] = await Promise.all([
    readFile(resolve(process.cwd(), "node_modules/page-flip/dist/js/page-flip.browser.js"), "utf8"),
    readFile(resolve(process.cwd(), "node_modules/page-flip/LICENSE"), "utf8"),
  ]);
  return new Response(`/*!\n${license}\n*/\n${script}`, { headers: { "Content-Type": "text/javascript; charset=utf-8", "Cache-Control": "public, max-age=86400", "X-Content-Type-Options": "nosniff" } });
}
