import { chromium } from "@playwright/test";
import { execFileSync } from "node:child_process";
import fs from "node:fs/promises";
import path from "node:path";
import assert from "node:assert/strict";
import { profileSchema } from "./lib/models.ts";

const root = path.resolve("..");
const selected = JSON.parse(await fs.readFile(path.join(root, ".intermediate/validation/selection.json"), "utf8"));
const code = execFileSync(path.join(root, "backend/.venv/bin/python"), ["-c", "from app.core.config import get_settings; print(get_settings().invite_code)"], { cwd: path.join(root, "backend"), encoding: "utf8" }).trim();
const browser = await chromium.launch({ channel: "chrome" });
const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
const page = await context.newPage();
const errors = [];
page.on("pageerror", error => errors.push(error.message));
page.on("response", response => { if (response.status() >= 400) console.log(`HTTP ${response.status()} ${new URL(response.url()).pathname}`); });
page.on("console", message => { if (message.type() === "error") errors.push(message.text()); });
const base = process.env.CHECK_URL || "http://127.0.0.1:3012";
const readOnly = process.env.CHECK_READ_ONLY === "1";
const evidence = path.join(root, "docs/evidence/阶段3-1");
await fs.mkdir(evidence, { recursive: true });
await page.goto(`${base}/login`);
await page.getByLabel("邀请码", { exact: true }).fill(code);
await page.getByRole("button", { name: "翻开我的回忆录" }).click();
await page.waitForURL(/\/profiles(?:\/[^/?]+)?$/);
const profileData = profileSchema.parse(await (await context.request.get(`${base}/api/v1/profiles/${selected.profile_id}`)).json());
if (readOnly) selected.chapter_id = profileData.calls.flatMap(call => call.chapters)[0].id;
const profiles = (await (await context.request.get(`${base}/api/v1/profiles`)).json()).items;
for (const profile of profiles) {
  const photos = (await (await context.request.get(`${base}/api/v1/profiles/${profile.id}/photos`)).json()).items;
  if (!selected.photo_id && photos.length) selected.photo_id = photos[0].id;
  if (photos.some(photo => photo.id === selected.photo_id)) selected.photo_profile_id = profile.id;
}
assert.ok(selected.photo_profile_id, "需要已有真实照片");
await page.getByText("正在翻开回忆…").waitFor({ state: "hidden" });
const routes = [
  ["回忆录首页", `/profiles/${selected.profile_id}`],
  ["章节阅读", `/profiles/${selected.profile_id}/chapters/${selected.chapter_id}`],
  ["相册", `/profiles/${selected.photo_profile_id || selected.profile_id}?tab=photos`],
  ["我的", `/account?profile=${selected.profile_id}`],
  ["通话", `/profiles/${selected.profile_id}/call`],
  ["家人", `/profiles/${selected.profile_id}/family`],
  ["建档", "/profiles?new=1"],
];
for (const width of [360, 375, 390, 430]) {
  await page.setViewportSize({ width, height: 844 });
  for (const [name, route] of routes) {
    await page.goto(base + route);
    await page.getByText("正在翻开回忆…").waitFor({ state: "hidden" });
    await page.locator("main").waitFor();
    const metrics = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, width: innerWidth, inputs: [...document.querySelectorAll("input:not([type=hidden]),textarea")].every(el => el.labels?.length || el.getAttribute("aria-label")) }));
    assert.ok(metrics.scroll <= metrics.width, `${name} ${width} 横向溢出`);
    assert.ok(metrics.inputs, `${name} 表单缺少标签`);
    assert.equal(await page.locator(".notice.error").count(), 0, `${name} 出现错误提示`);
    await page.screenshot({ path: path.join(evidence, `${name}-${width}px.png`), fullPage: true });
    console.log(`通过：${name} ${width}px`);
  }
}
await page.goto(`${base}/profiles/${selected.profile_id}/chapters/${selected.chapter_id}`);
await page.getByText("看看这段原话").first().click();
assert.ok((await page.locator("blockquote").first().innerText()).length > 0);
await page.reload();
await page.locator(".reading-body").waitFor();
await page.goto(`${base}/profiles/${selected.profile_id}/call`);
await page.getByRole("button", { name: "开始聊聊" }).click();
await page.getByText("请由老人本人勾选同意录音后再开始。").waitFor();
assert.equal(await page.evaluate(() => document.querySelectorAll("audio").length), 0);
if (!readOnly) {
await page.goto(`${base}/profiles?new=1`);
await page.getByLabel("怎么称呼您").fill("手机验收档案");
await page.getByText("补充背景与不想聊的话题（选填）", { exact: true }).click();
await page.getByLabel("家乡", { exact: true }).fill("绍兴");
await page.getByRole("button", { name: "开始这本回忆录" }).click();
await page.waitForURL(/\/profiles\/[^/?]+$/);
const createdId = new URL(page.url()).pathname.split("/").at(-1);
await page.getByRole("button", { name: "修改书名" }).click();
await page.getByLabel("书名", { exact: true }).fill("留给家人的回忆");
await page.getByRole("button", { name: "保存书名" }).click();
await page.getByText("留给家人的回忆", { exact: true }).waitFor();
await page.reload();
await page.getByText("留给家人的回忆", { exact: true }).waitFor();
const originalPhoto = await context.request.get(`${base}/api/v1/photos/${selected.photo_id}`);
assert.ok(originalPhoto.ok());
await page.goto(`${base}/profiles/${createdId}?tab=photos`);
await page.getByLabel("上传老照片").setInputFiles({ name: "真实照片验收.jpg", mimeType: originalPhoto.headers()["content-type"], buffer: await originalPhoto.body() });
await page.getByRole("link", { name: "从这张照片聊起" }).click();
await page.getByAltText("本次访谈选择的老照片").waitFor();
assert.ok(new URL(page.url()).searchParams.get("photo"));
await page.goto(`${base}/profiles/${createdId}/edit`);
await page.getByLabel("家乡", { exact: true }).fill("绍兴老家");
await page.getByRole("button", { name: "保存资料" }).click();
await page.waitForURL(`${base}/profiles/${createdId}`);
assert.equal((await (await context.request.get(`${base}/api/v1/profiles/${createdId}`)).json()).hometown, "绍兴老家");
}
assert.deepEqual(errors, []);
await fs.writeFile(path.join(root, `.intermediate/validation/browser-${readOnly ? "production" : "result"}.json`), JSON.stringify({ widths: [360,375,390,430], pages: routes.length, consoleErrors: errors, source: readOnly ? "正式服务只读验证" : "真实访谈数据库隔离副本", passed: true }));
await browser.close();
console.log(readOnly ? "正式服务通过：28 个手机页面检查、原话阅读、刷新恢复、录音同意保护" : "浏览器检查通过：28 个手机页面检查、原话阅读、刷新恢复、录音同意保护、建档、书名和资料编辑、真实照片上传及访谈入口")
