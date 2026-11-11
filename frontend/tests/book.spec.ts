import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const books = JSON.parse(readFileSync(resolve(process.cwd(), "../data/showcase-books.json"), "utf8")) as { key: string; chapters: { paragraphs: string[] }[] }[];
const origin = "http://127.0.0.1:3012";
test.use({ channel: "chrome" });

for (const width of [320, 390, 430]) {
  test(`第二版六页引导在 ${width}px 下完整连接`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 });
    await page.goto(`${origin}/welcome`);
    for (let step = 0; step < 6; step++) {
      await expect(page.locator('[data-design="book-second"]')).toBeVisible();
      await expect(page.locator('.guide-folio')).toHaveText(String(step + 1).padStart(2, '0'));
      await expect.poll(() => page.locator('.guide-leaf').evaluate(element => getComputedStyle(element).transform)).toBe('none');
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      const geometry = await page.locator('.guide-leaf').evaluate(element => {
        const leaf = element.getBoundingClientRect();
        const parts = Array.from(element.querySelectorAll('.guide-copy > *, .guide-cover > h1')).map(node => node.getBoundingClientRect());
        return parts.every(part => part.left >= leaf.left && part.right <= leaf.right && part.bottom <= leaf.bottom - 30);
      });
      expect(geometry).toBe(true);
      if (step === 3) {
        await expect(page.getByRole('link', { name: '李奶奶的故事 ›' })).toHaveAttribute('href', '/books/li');
        await expect(page.getByRole('link', { name: '杨奶奶的故事 ›' })).toHaveAttribute('href', '/books/yang');
      }
      if (step < 5) await page.locator('.guide-controls .primary').click();
    }
    await page.getByRole('link', { name: '为自己记录' }).click();
    await expect(page).toHaveURL(/login\?for=self/);
  });
  test(`真实回忆录在 ${width}px 下阅读和翻页`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 });
    await page.goto(`${origin}/welcome?step=3`);
    await page.getByRole("link", { name: "李奶奶的故事 ›" }).click();
    await expect(page.getByRole("link", { name: "翻开阅读" })).toBeVisible();
    await page.getByRole("link", { name: "查看目录" }).click();
    await expect(page.locator(".printed-contents li")).toHaveCount(7);
    for (const book of books) {
      for (const [chapter, content] of book.chapters.entries()) {
        await page.goto(`${origin}/books/${book.key}?view=read&chapter=${chapter}`);
        await expect(page.locator(".printed-columns p")).toHaveCount(content.paragraphs.length);
        expect((await page.locator(".printed-columns p").allTextContents()).join("") === content.paragraphs.join("")).toBe(true);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        const bounds = await page.locator(".page-window").boundingBox();
        expect(bounds!.width).toBeGreaterThan(200);
        expect(bounds!.height).toBeGreaterThan(200);
      }
    }
    await page.goto(`${origin}/books/li?view=read&chapter=0`);
    await expect(page.locator(".printed-folio")).not.toHaveText("1 / 1");
    const count = Number((await page.locator(".printed-folio").innerText()).split(" / ")[1]);
    for (let index = 1; index < count; index++) await page.getByRole("button", { name: "下一页", exact: true }).click();
    await expect(page.getByRole("link", { name: "下一章", exact: true })).toBeVisible();
    await expect.poll(() => page.evaluate(() => {
      const viewport = document.querySelector(".page-window")!.getBoundingClientRect();
      const last = document.querySelector(".printed-columns p:last-child")!.firstChild!;
      const range = document.createRange();
      range.setStart(last, last.textContent!.length - 1); range.setEnd(last, last.textContent!.length);
      const character = range.getBoundingClientRect();
      return character.left >= viewport.left - 1 && character.right <= viewport.right + 1 && character.top >= viewport.top - 1 && character.bottom <= viewport.bottom + 1;
    })).toBe(true);
    await page.getByRole("button", { name: "大字" }).click();
    await expect(page.getByRole("button", { name: "大字" })).toHaveAttribute("aria-pressed", "true");
    expect(Number((await page.locator(".printed-folio").innerText()).split(" / ")[1])).toBeGreaterThanOrEqual(count);
    await page.getByRole("link", { name: "目录", exact: true }).click();
    await expect(page.locator(".printed-contents")).toBeVisible();
  });
}

test("无效书名不会读取任意文件", async ({ request }) => {
  const response = await request.get(`${origin}/books/unknown`);
  expect(response.status()).toBe(404);
});
