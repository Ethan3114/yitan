import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const books = JSON.parse(readFileSync(resolve(process.cwd(), "../data/showcase-books.json"), "utf8")) as { key: string; chapters: { paragraphs: string[] }[] }[];
const origin = process.env.CHECK_URL || "http://127.0.0.1:3012";
test.use({ channel: "chrome" });

for (const width of [320, 390, 430]) {
  test(`第二版六页引导在 ${width}px 下完整连接`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 });
    await page.goto(`${origin}/welcome`);
    for (let step = 0; step < 6; step++) {
      await expect(page.locator('[data-design="book-second"]')).toBeVisible();
      const active = page.frameLocator('.guide-page-book iframe').locator(`[data-leaf-index="${step}"][aria-hidden="false"]`);
      await expect(active.locator('.guide-folio')).toHaveText(String(step + 1).padStart(2, '0'));
      await expect(page.locator('.guide-page-book')).toHaveAttribute('data-engine', 'page-flip');
      await expect(page.locator('.guide-page-book')).toHaveAttribute('data-motion', 'read');
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      const geometry = await active.locator('.guide-leaf').evaluate(element => {
        const leaf = element.getBoundingClientRect();
        const parts = Array.from(element.querySelectorAll('.guide-copy > *, .guide-cover > h1')).map(node => node.getBoundingClientRect());
        return parts.every(part => part.left >= leaf.left && part.right <= leaf.right && part.bottom <= leaf.bottom - 30);
      });
      expect(geometry).toBe(true);
      if (step === 3) {
        await expect(page.frameLocator('.guide-page-book iframe').getByRole('link', { name: '李奶奶的故事 · 开篇节选 ›' })).toHaveAttribute('href', '/books/li');
      }
      if (step < 5) {
        await page.locator('.guide-controls .primary').click();
        await expect(page.locator('.guide-page-book')).toHaveAttribute('data-motion', 'flipping');
        await expect(page.locator('.guide-page-book')).toHaveAttribute('data-page-index', String(step + 1));
        await expect(page.locator('.guide-page-book')).toHaveAttribute('data-motion', 'read');
        await expect(page).toHaveURL(new RegExp(`welcome\\?step=${step + 1}$`));
      }
    }
    await page.frameLocator('.guide-page-book iframe').getByRole('link', { name: '为自己记录' }).click();
    await expect(page).toHaveURL(/login\?for=self/);
  });
  test(`真实回忆录在 ${width}px 下阅读和翻页`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 });
    await page.goto(`${origin}/welcome?step=3`);
    await page.frameLocator('.guide-page-book iframe').getByRole("link", { name: "李奶奶的故事 · 开篇节选 ›" }).click();
    await expect(page.getByRole("link", { name: "翻开阅读" })).toBeVisible();
    await page.getByRole("link", { name: "查看目录" }).click();
    await expect(page.locator(".printed-contents li")).toHaveCount(7);
    for (const book of books) {
      for (const [chapter, content] of book.chapters.entries()) {
        await page.goto(`${origin}/books/${book.key}?view=read&chapter=${chapter}`);
        await expect(page.locator('.reading-page-book')).toHaveAttribute('data-engine', 'page-flip');
        expect((await page.locator(".reading-binding > [hidden] .printed-page-body p").allTextContents()).join("") === content.paragraphs.join("")).toBe(true);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        const bounds = await page.frameLocator('.reading-page-book iframe').locator('[aria-hidden="false"] .page-window').boundingBox();
        expect(bounds!.width).toBeGreaterThan(200);
        expect(bounds!.height).toBeGreaterThan(200);
      }
    }
    await page.goto(`${origin}/books/li?view=read&chapter=0`);
    await expect(page.locator('.reading-page-book')).toHaveAttribute('data-engine', 'page-flip');
    const count = await page.locator('.reading-binding > [hidden] .turning-leaf').count();
    expect(count).toBeGreaterThan(1);
    for (let index = 1; index < count; index++) {
      await page.getByRole("button", { name: "下一页", exact: true }).click();
      await expect(page.locator('.reading-page-book')).toHaveAttribute('data-motion', 'flipping');
      await expect(page.locator('.reading-page-book')).toHaveAttribute('data-page-index', String(index));
      await expect(page.locator('.reading-page-book')).toHaveAttribute('data-motion', 'read');
    }
    await expect(page.getByRole("link", { name: "下一章", exact: true })).toBeVisible();
    await expect.poll(() => page.evaluate(() => {
      const active = document.querySelector<HTMLIFrameElement>('.reading-page-book iframe')!.contentDocument!.querySelector('[aria-hidden="false"]')!;
      const viewport = active.querySelector(".page-window")!.getBoundingClientRect();
      const last = active.querySelector(".printed-page-body p:last-child")!.firstChild!;
      const range = document.createRange();
      range.setStart(last, last.textContent!.length - 1); range.setEnd(last, last.textContent!.length);
      const character = range.getBoundingClientRect();
      return character.left >= viewport.left - 1 && character.right <= viewport.right + 1 && character.top >= viewport.top - 1 && character.bottom <= viewport.bottom + 1;
    })).toBe(true);
    await page.getByRole("button", { name: "大字" }).click();
    await expect(page.getByRole("button", { name: "大字" })).toHaveAttribute("aria-pressed", "true");
    await expect(page.frameLocator('.reading-page-book iframe').locator('[aria-hidden="false"] .large-type .printed-page-body')).toHaveCSS('font-size', '24px');
    await expect.poll(() => page.locator('.reading-binding > [hidden] .turning-leaf').count()).toBeGreaterThanOrEqual(count);
    await expect(page.locator('.reading-page-book')).toHaveAttribute('data-engine', 'page-flip');
    expect((await page.locator('.reading-binding > [hidden] .printed-page-body p').allTextContents()).join('') === books[0].chapters[0].paragraphs.join('')).toBe(true);
    await page.getByRole("link", { name: "目录", exact: true }).click();
    await expect(page.locator(".printed-contents")).toBeVisible();
  });
}

test("无效书名不会读取任意文件", async ({ request }) => {
  const response = await request.get(`${origin}/books/unknown`);
  expect(response.status()).toBe(404);
});
