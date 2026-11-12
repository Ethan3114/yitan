import { readFile, mkdir, writeFile, chmod } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "../frontend/node_modules/playwright/index.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const origin = process.argv[2];
if (!origin?.startsWith("https://")) throw new Error("必须使用真实公网 HTTPS 地址");
const admin = JSON.parse(await readFile(path.join(root, ".intermediate/private/cloud-admin.json"), "utf8"));
const migration = JSON.parse(await readFile(path.join(root, "data/migration/li/manifest.json"), "utf8"));
const expectedSample = JSON.parse(await readFile(path.join(root, "frontend/content/public-samples.json"), "utf8"))[0];
const evidenceDirectory = path.join(root, "docs/evidence/阶段4");
await mkdir(evidenceDirectory, { recursive: true });
const browser = await chromium.launch({ channel: "chrome", headless: true });
const checks = [];
const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
const page = await context.newPage();
const errors = [];
const requests = [];
let stage = "brand";
page.on("pageerror", error => errors.push(error.name));
page.on("response", response => {
  if (["script", "fetch", "xhr"].includes(response.request().resourceType())) requests.push({ path: new URL(response.url()).pathname, status: response.status(), type: response.headers()["content-type"] });
});

async function visit(route) {
  await page.goto(origin + route, { waitUntil: "domcontentloaded", timeout: 120000 });
  await page.waitForFunction(() => document.querySelector("main") || document.body.innerText.includes("确定访问"), { timeout: 90000 });
  if (!await page.locator("main").count()) await page.waitForTimeout(3500);
  const confirmation = page.getByText("确定访问", { exact: true });
  if (await confirmation.count()) {
    await confirmation.waitFor({ state: "visible", timeout: 10000 });
    await page.waitForTimeout(3500);
    await confirmation.click();
    await page.waitForLoadState("domcontentloaded");
  }
}

try {
  await visit("/welcome");
  await page.getByRole("main").waitFor();
  await page.screenshot({ path: path.join(evidenceDirectory, "首页-390px.png"), animations: "disabled" });
  checks.push("公网品牌引导能够显示");
  stage = "unhydrated_forms";
  const noScript = await browser.newContext({ javaScriptEnabled: false, storageState: await context.storageState() });
  try {
    const plain = await noScript.newPage();
    for (const route of ["/login", "/register", "/recover"]) {
      await plain.goto(origin + route, { waitUntil: "domcontentloaded", timeout: 90000 });
      const form = plain.locator("form.form");
      if (await form.getAttribute("method") !== "post" || !await form.locator("button.primary").isDisabled()) throw new Error("敏感表单在脚本加载前未被保护");
    }
    checks.push("关闭JavaScript时敏感表单保持POST且禁止提交");
  } finally {
    await noScript.close();
  }
  stage = "public_excerpt";
  await visit("/books/li?view=read&chapter=0");
  const paragraphs = await page.locator(".printed-columns p").allTextContents();
  if (JSON.stringify(paragraphs) !== JSON.stringify(expectedSample.chapters[0].paragraphs)) throw new Error("公开节选与授权正文不一致");
  checks.push("匿名样书仅包含379字授权节选");
  stage = "login";
  await visit("/login");
  await page.getByLabel("账号", { exact: true }).fill(admin.username);
  await page.getByLabel("密码", { exact: true }).fill(admin.password);
  await page.getByRole("button", { name: "翻开我的回忆录" }).click();
  await page.waitForURL(url => !url.pathname.endsWith("/login"), { timeout: 90000 });
  checks.push("公网浏览器真实管理员登录");
  stage = "private_book";
  await visit(`/profiles/${migration.cloud_profile_id}/book`);
  await page.getByText("7 章 · 5,366 字", { exact: true }).waitFor({ timeout: 90000 });
  checks.push("管理员家庭完整七章5366字成书可见");
  const readingEvent = page.waitForResponse(response => response.request().method() === "POST" && /\/api\/v1\/chapters\/[^/]+\/read$/.test(response.url()), { timeout: 90000 });
  await visit(`/profiles/${migration.cloud_profile_id}/book?view=read&chapter=0`);
  await page.locator(".printed-columns p").first().waitFor({ timeout: 90000 });
  if ((await readingEvent).status() !== 200) throw new Error("个人回忆录阅读事件没有真实保存");
  checks.push("私有回忆录正文能够翻开阅读");
  checks.push("主阅读入口上报真实阅读事件");
  stage = "admin";
  await visit("/admin");
  await page.getByRole("heading", { name: "找到用户停下的位置" }).waitFor({ timeout: 90000 });
  await page.getByRole("button", { name: "分阶段转化" }).click({ timeout: 90000 });
  await page.locator(".admin-funnel li").first().waitFor({ timeout: 90000 });
  for (const width of [320, 360, 375, 390, 430]) {
    await page.setViewportSize({ width, height: 844 });
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
    if (overflow) throw new Error(`后台页面${width}px出现横向溢出`);
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: path.join(evidenceDirectory, "管理转化-390px.png"), fullPage: true, animations: "disabled" });
  checks.push("公网分阶段人数及五种手机宽度正常");
  const anonymous = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const denied = await anonymous.request.get(`${origin}/api/v1/profiles/${migration.cloud_profile_id}`);
  if (![401, 404].includes(denied.status())) throw new Error("完整私人回忆录允许匿名读取");
  checks.push("完整回忆录匿名读取被拒绝");
  await anonymous.close();
  if (errors.length) throw new Error("公网页面出现脚本错误：" + errors.join(","));
  const directory = path.join(root, ".intermediate/public-browser");
  await mkdir(directory, { recursive: true });
  const output = path.join(directory, "result.json");
  await writeFile(output, JSON.stringify({ checked_at: new Date().toISOString(), origin, passed: checks.length, checks, page_errors: errors }, null, 2));
  await chmod(output, 0o600);
  console.log(JSON.stringify({ passed: checks.length, page_errors: 0 }));
} catch (error) {
  console.error(JSON.stringify({ failed: true, error_name: error.name, stage, path: new URL(page.url()).pathname, page_errors: errors, requests }));
  process.exitCode = 1;
} finally {
  await context.close();
  await browser.close();
}
