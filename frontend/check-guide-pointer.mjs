import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { chromium } from "@playwright/test";

const origin = process.env.CHECK_URL || "http://127.0.0.1:3023";
const browser = await chromium.launch({ channel: "chrome", headless: true });
const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
const page = await context.newPage();
const checks = [];
const errors = [];
page.on("pageerror", error => errors.push(error.name));
let stage = "loading";

async function ready(index) {
  await page.waitForFunction(index => {
    const book = document.querySelector(".guide-page-book");
    return book?.dataset.engine === "page-flip" && book.dataset.motion === "read" && book.dataset.pageIndex === String(index);
  }, index);
  await page.waitForFunction(() => !document.querySelector(".guide-controls .primary")?.disabled);
}

async function go(index, backwards = false) {
  const button = backwards ? page.getByRole("button", { name: "上一页", exact: true }) : page.locator(".guide-controls .primary");
  await button.click();
  await page.waitForFunction(() => document.querySelector(".guide-page-book")?.dataset.motion === "flipping");
  assert.equal(await button.isDisabled(), true, "实际翻动期间按钮锁定");
  await ready(index);
  assert.equal(await page.locator(".guide-controls .primary").isEnabled(), true);
}

try {
  for (const width of [320, 360, 375, 390, 430, 1280]) {
    stage = `hover_${width}`;
    await page.setViewportSize({ width, height: 1000 });
    await page.goto(origin + "/welcome");
    await ready(0);
    const frame = page.frameLocator(".guide-page-book iframe");
    const size = await frame.locator('[data-leaf-index="0"] .guide-leaf').evaluate(element => getComputedStyle(element).backgroundSize);
    assert.equal(size, "100% 100%, cover", "封面插画使用原始比例覆盖纸面");
    for (const corner of ["top", "bottom"]) {
      const bounds = await page.locator(".guide-page-book iframe").boundingBox();
      await page.mouse.move(bounds.x + bounds.width - 4, bounds.y + (corner === "top" ? 4 : bounds.height - 4));
      await page.waitForTimeout(250);
      assert.equal(await page.locator(".guide-page-book").getAttribute("data-motion"), "read", "悬停页角保持阅读状态");
      assert.equal(await page.locator(".guide-page-book").getAttribute("aria-busy"), "false");
      assert.equal(await page.locator(".guide-controls .primary").isEnabled(), true, "悬停页角后下一页仍可操作");
      await go(1);
      await go(0, true);
    }
    checks.push(`${width}px右上右下悬停不锁定按钮，按钮连续正反翻页正常`);
  }

  stage = "scene_ratios";
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(origin + "/welcome?step=1");
  await ready(1);
  for (const index of [1, 2, 3, 4]) {
    const imageLayer = await page.frameLocator(".guide-page-book iframe").locator(`[data-leaf-index="${index}"] .guide-leaf`).evaluate(element => getComputedStyle(element).backgroundSize);
    assert.equal(imageLayer, "100% 100%, cover", "引导场景插画保持原始比例");
    if (index < 4) await go(index + 1);
  }
  checks.push("各引导场景在实际iframe中均使用等比背景");

  stage = "drag_outside";
  await page.goto(origin + "/welcome?step=1");
  await ready(1);
  const bounds = await page.locator(".guide-page-book iframe").boundingBox();
  await page.mouse.move(bounds.x + bounds.width - 12, bounds.y + bounds.height - 12);
  await page.mouse.down();
  await page.mouse.move(bounds.x + bounds.width - 90, bounds.y + bounds.height - 45, { steps: 12 });
  await page.waitForFunction(() => document.querySelector(".guide-page-book")?.dataset.motion === "user_fold");
  await page.mouse.move(bounds.x - 28, bounds.y + bounds.height - 45, { steps: 20 });
  await page.mouse.up();
  await page.waitForFunction(() => document.querySelector(".guide-page-book")?.dataset.motion === "read", undefined, { timeout: 5000 });
  assert.equal(await page.locator(".guide-controls .primary").isEnabled(), true, "拖到纸面外释放后按钮恢复");
  checks.push("真实鼠标拖出纸面并释放后正常结束手势");
  assert.deepEqual(errors, []);
  await mkdir("../.intermediate/guide-pointer", { recursive: true });
  await writeFile("../.intermediate/guide-pointer/result.json", JSON.stringify({ checked_at: new Date().toISOString(), origin, passed: checks.length, checks, page_errors: errors, model_calls: 0 }, null, 2));
  console.log(JSON.stringify({ passed: checks.length, page_errors: errors, model_calls: 0 }));
} catch (error) {
  console.error(JSON.stringify({ stage, error_name: error.name, checks_passed: checks.length, page_errors: errors }));
  process.exitCode = 1;
} finally {
  await context.close();
  await browser.close();
}
