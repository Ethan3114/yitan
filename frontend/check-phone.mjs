import { chromium } from "@playwright/test";
import { execFileSync } from "node:child_process";
import fs from "node:fs/promises";
import path from "node:path";
import assert from "node:assert/strict";

const root = path.resolve("..");
const code = execFileSync(path.join(root, "backend/.venv/bin/python"), ["-c", "from app.core.config import get_settings; print(get_settings().invite_code)"], { cwd: path.join(root, "backend"), encoding: "utf8" }).trim();
const browser = await chromium.launch({ channel: "chrome" });
// 证书链由 check_phone_https.py 独立严格验证；此处检查页面布局。
const context = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
const page = await context.newPage();
const errors = [];
page.on("pageerror", error => errors.push(error.message));
const base = "https://127.0.0.1:3443";
await page.goto(`${base}/login`);
await page.getByLabel("邀请码", { exact: true }).fill(code);
await page.getByRole("button", { name: "翻开我的回忆录" }).click();
await page.waitForURL(/\/profiles(?:\/[^/?]+)?$/);
const profiles = await (await context.request.get(`${base}/api/v1/profiles`)).json();
const profile = profiles.items.find(item => item.id === "f919fddd-be5f-56ed-8681-b48d64af6e63");
assert.ok(profile);
const evidence = path.join(root, "docs/evidence/阶段3-3");
await fs.mkdir(evidence, { recursive: true });
for (const width of [360, 375, 390, 430]) {
  await page.setViewportSize({ width, height: 844 });
  for (const [label, route] of [["回忆录", ""], ["访谈", "/call"], ["家人", "/family"]]) {
    await page.goto(`${base}/profiles/${profile.id}${route}`, { waitUntil: "networkidle" });
    assert.equal(await page.locator(".notice.error").count(), 0);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.screenshot({ path: path.join(evidence, `${label}-${width}px.png`), fullPage: true });
  }
}
await page.goto(`${base}/profiles/${profile.id}/call`, { waitUntil: "networkidle" });
await page.getByRole("button", { name: "开始聊聊" }).click();
await page.getByText("请由老人本人勾选同意录音后再开始。", { exact: true }).waitFor();
assert.equal(errors.length, 0);
await context.request.post(`${base}/api/v1/auth/logout`);
await browser.close();
console.log("HTTPS 页面 3 类 × 4 种手机宽度通过，未同意录音阻止开始，页面异常为零；未测试实体麦克风。");
