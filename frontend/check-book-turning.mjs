import { chromium } from "@playwright/test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";

const base = process.env.CHECK_URL || "http://127.0.0.1:3021";
const publicOnly = process.env.CHECK_PUBLIC === "1";
const originals = JSON.parse(await fs.readFile("../data/showcase-books.json", "utf8"));
const samples = JSON.parse(await fs.readFile("content/public-samples.json", "utf8"));
assert.equal(samples.length, 1);
assert.equal(samples[0].chapters.length, 1);
assert.deepEqual(samples[0].chapters[0].paragraphs, [originals[0].chapters[0].paragraphs[0]]);
assert.equal(samples[0].characters, 216);
const books = publicOnly ? samples : originals;
const browser = await chromium.launch({ channel: "chrome" });
const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
const page = await context.newPage();
const errors = [];
const results = { widths: [], guideTurns: 0, bodyTurns: 0, chapters: 0, publicCharacters: 216, exactOpening: true, errors };
page.on("pageerror", error => errors.push(error.message));

async function turn(button, expected, selector = ".guide-page-book") {
  await page.evaluate(selector => {
    const book = document.querySelector(selector);
    const audit = { states: [], frames: 0, start: performance.now(), end: 0, stopped: false, curved: false };
    window.bookTurnAudit = audit;
    const observer = new MutationObserver(() => {
      audit.states.push(book.dataset.motion);
      if (book.dataset.motion === "read") audit.end = performance.now();
    });
    observer.observe(book, { attributes: true, attributeFilter: ["data-motion"] });
    function frame() {
      if (audit.stopped) { observer.disconnect(); return; }
      if (book.dataset.motion === "flipping") {
        audit.frames++;
        audit.curved ||= [...book.querySelector("iframe").contentDocument.querySelectorAll(".stf__item")].some(leaf => {
          const style = getComputedStyle(leaf);
          return style.transform !== "none" || style.clipPath !== "none";
        });
      }
      requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
  }, selector);
  await button.click();
  const diagnostic = setTimeout(async () => console.log("翻页诊断", await page.evaluate(selector => ({ expectedAudit: window.bookTurnAudit, index: document.querySelector(selector)?.dataset.pageIndex, motion: document.querySelector(selector)?.dataset.motion, url: location.pathname + location.search }), selector)), 5000);
  await page.waitForFunction(({ selector, expected }) => {
    const book = document.querySelector(selector);
    return book.dataset.pageIndex === String(expected) && book.dataset.motion === "read";
  }, { selector, expected });
  clearTimeout(diagnostic);
  const audit = await page.evaluate(() => { window.bookTurnAudit.stopped = true; return window.bookTurnAudit; });
  assert.ok(audit.states.includes("flipping"), "书页进入连续翻动状态");
  assert.ok(audit.frames >= 5, `翻页包含连续动画帧：${JSON.stringify(audit)}`);
  assert.ok(audit.curved, "纸面存在翻转或卷曲变换");
  assert.ok(audit.end - audit.start >= 500, `保留完整的翻页过程：${JSON.stringify(audit)}`);
}

for (const width of process.env.CHECK_WIDTH ? [Number(process.env.CHECK_WIDTH)] : [320, 360, 375, 390, 430]) {
  await page.setViewportSize({ width, height: 844 });
  await page.goto(`${base}/welcome`);
  await page.locator('.guide-page-book[data-engine="page-flip"]').waitFor();
  for (let step = 0; step < 6; step++) {
    const state = await page.evaluate(() => {
      const book = document.querySelector(".guide-page-book");
      const leaf = book.querySelector("iframe").contentDocument.querySelector(`[data-leaf-index="${book.dataset.pageIndex}"][aria-hidden="false"]`);
      const paper = leaf.querySelector(".guide-leaf").getBoundingClientRect();
      const parts = [...leaf.querySelectorAll(".guide-copy > *, .guide-cover > h1, .guide-cover > p")].map(part => part.getBoundingClientRect());
      return { index: Number(book.dataset.pageIndex), overflow: document.documentElement.scrollWidth > innerWidth, inside: parts.every(part => part.left >= paper.left - 1 && part.right <= paper.right + 1 && part.bottom <= paper.bottom - 30), background: getComputedStyle(leaf.querySelector(".guide-leaf")).backgroundImage, geometry: { paper: paper.toJSON(), parts: parts.map(part => part.toJSON()) } };
    });
    assert.equal(state.index, step);
    assert.equal(state.overflow, false, `${width}px 第 ${step + 1} 页无横向溢出`);
    assert.equal(state.inside, true, `${width}px 第 ${step + 1} 页内容在纸面内：${JSON.stringify(state.geometry)}`);
    if (!step) assert.ok(state.background.includes("courtyard.png"));
    if (step < 5) { await turn(page.locator(".guide-controls .primary"), step + 1); results.guideTurns++; }
  }
  assert.match(page.url(), /welcome\?step=5$/);
  await turn(page.getByRole("button", { name: "上一页", exact: true }), 4);
  results.guideTurns++;
  await page.goBack();
  await page.waitForFunction(() => document.querySelector(".guide-page-book").dataset.pageIndex === "5");
  results.widths.push(width);
}

await page.setViewportSize({ width: 390, height: 844 });
await page.goto(`${base}/welcome?step=1`);
await page.locator('.guide-page-book[data-engine="page-flip"]').waitFor();
await page.frameLocator(".guide-page-book iframe").locator("body").press("ArrowRight");
await page.waitForFunction(() => {
  const book = document.querySelector(".guide-page-book");
  return book.dataset.pageIndex === "2" && book.dataset.motion === "read";
});
await page.frameLocator(".guide-page-book iframe").locator("body").press("ArrowLeft");
await page.waitForFunction(() => {
  const book = document.querySelector(".guide-page-book");
  return book.dataset.pageIndex === "1" && book.dataset.motion === "read";
});
results.keyboard = true;
const touch = await context.newCDPSession(page);
const rectangle = await page.locator(".guide-page-book iframe").boundingBox();
const start = { x: rectangle.x + rectangle.width - 12, y: rectangle.y + rectangle.height - 26 };
await touch.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [start] });
for (let index = 1; index <= 5; index++) {
  await touch.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x: start.x - index * 45, y: start.y - 5 }] });
  await page.waitForTimeout(20);
}
await touch.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
await page.waitForFunction(() => {
  const book = document.querySelector(".guide-page-book");
  return book.dataset.pageIndex === "2" && book.dataset.motion === "read";
});
results.touchSwipe = true;
await page.evaluate(() => {
  window.bookFrameTicks = 0;
  window.bookActivityEvents = 0;
  window.bookActivityListener = () => window.bookActivityEvents++;
  window.addEventListener("yitan:book-activity", window.bookActivityListener);
  window.oldBookWindow = document.querySelector(".guide-page-book iframe").contentWindow;
});
const child = page.frames().find(frame => frame.parentFrame() === page.mainFrame());
await child.evaluate(() => {
  function tick() { parent.bookFrameTicks++; requestAnimationFrame(tick); }
  requestAnimationFrame(tick);
});
await page.waitForFunction(() => window.bookFrameTicks >= 5);
await page.frameLocator(".guide-page-book iframe").locator("body").press("Tab");
await page.waitForFunction(() => window.bookActivityEvents > 0);
await page.getByRole("link", { name: "已有回忆录", exact: true }).click();
await page.waitForURL("**/login");
await page.waitForFunction(() => window.oldBookWindow.closed);
assert.equal(child.isDetached(), true);
const retiredTicks = await page.evaluate(() => window.bookFrameTicks);
await page.waitForTimeout(200);
assert.equal(await page.evaluate(() => window.bookFrameTicks), retiredTicks, "卸载后子页面的动画帧停止");
assert.equal(await page.evaluate(() => window.oldBookWindow.closed), true, "书页浏览上下文已关闭");
await page.evaluate(() => {
  window.removeEventListener("yitan:book-activity", window.bookActivityListener);
  delete window.oldBookWindow;
});
results.unmountedFramesStopped = true;
results.bookActivity = true;

await page.goto(`${base}/welcome?step=3`);
await page.frameLocator(".guide-page-book iframe").getByRole("link", { name: "李奶奶的故事 · 开篇节选 ›", exact: true }).click();
await page.getByRole("link", { name: "翻开阅读", exact: true }).waitFor();
for (const book of books) {
  for (const [chapter, content] of book.chapters.entries()) {
    await page.goto(`${base}/books/${book.key}?view=read&chapter=${chapter}`);
    await page.locator('.reading-page-book[data-engine="page-flip"]').waitFor();
    const paragraphs = await page.locator(".reading-binding > [hidden] .printed-page-body p").allTextContents();
    assert.equal(paragraphs.join("") === content.paragraphs.join(""), true, "真实章节分页前后逐字一致");
    const count = await page.locator(".reading-binding > [hidden] .turning-leaf").count();
    for (let index = 0; index < count; index++) {
      const inside = await page.evaluate(() => {
        const book = document.querySelector(".reading-page-book");
        const leaf = book.querySelector("iframe").contentDocument.querySelector(`[data-leaf-index="${book.dataset.pageIndex}"][aria-hidden="false"]`);
        const viewport = leaf.querySelector(".page-window").getBoundingClientRect();
        return [...leaf.querySelectorAll(".printed-page-body p")].every(paragraph => {
          const range = document.createRange();
          range.selectNodeContents(paragraph);
          return [...range.getClientRects()].every(rect => rect.left >= viewport.left - 1 && rect.right <= viewport.right + 1 && rect.top >= viewport.top - 1 && rect.bottom <= viewport.bottom + 1);
        });
      });
      assert.equal(inside, true, "当前正文页的每个字完整位于纸面");
      if (index + 1 < count) { await turn(page.getByRole("button", { name: "下一页", exact: true }), index + 1, ".reading-page-book"); results.bodyTurns++; }
    }
    await page.getByRole("button", { name: "大字", exact: true }).click();
    await page.frameLocator('.reading-page-book iframe').locator('[aria-hidden="false"] .large-type .printed-page-body').waitFor();
    assert.equal(await page.frameLocator('.reading-page-book iframe').locator('[aria-hidden="false"] .large-type .printed-page-body').evaluate(element => getComputedStyle(element).fontSize), "24px");
    assert.equal(await page.getByRole("button", { name: "大字", exact: true }).getAttribute("aria-pressed"), "true");
    const large = await page.locator(".reading-binding > [hidden] .printed-page-body p").allTextContents();
    assert.equal(large.join("") === content.paragraphs.join(""), true, "大字分页保留完整原文");
    results.chapters++;
  }
}

await page.emulateMedia({ reducedMotion: "reduce" });
await page.goto(`${base}/welcome`);
await page.locator('.guide-page-book[data-engine="page-flip"]').waitFor();
await page.getByRole("button", { name: "翻开这本书", exact: true }).click();
await page.waitForFunction(() => document.querySelector(".guide-page-book").dataset.pageIndex === "1");
assert.equal(await page.locator(".guide-page-book").getAttribute("data-motion"), "read");
results.reducedMotion = true;
assert.deepEqual(errors, []);
await fs.mkdir("../.intermediate/book-turning", { recursive: true });
await fs.writeFile(`../.intermediate/book-turning/${publicOnly ? "public" : "local"}-result.json`, JSON.stringify(results, null, 2));
await browser.close();
console.log(JSON.stringify(results));
