import { readFile, mkdir, writeFile, chmod } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "../frontend/node_modules/playwright/index.mjs";
import assert from "node:assert/strict";

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
  await page.locator('.page-book[data-engine="page-flip"][data-motion="read"]').waitFor();
  await page.screenshot({ path: path.join(evidenceDirectory, "首页-390px.png"), animations: "disabled" });
  checks.push("公网品牌引导能够显示");
  stage = "continuous_page_turn";
  for (let step = 1; step <= 5; step += 1) {
    await page.locator(".guide-controls button.primary").click();
    await page.waitForFunction(index => {
      const book = document.querySelector(".guide-page-book");
      return book?.getAttribute("data-page-index") === String(index) && book.getAttribute("data-motion") === "read";
    }, step);
    assert.equal(new URL(page.url()).searchParams.get("step"), String(step));
  }
  await page.getByRole("button", { name: "上一页", exact: true }).click();
  await page.waitForFunction(() => document.querySelector(".guide-page-book")?.getAttribute("data-page-index") === "4" && document.querySelector(".guide-page-book")?.getAttribute("data-motion") === "read");
  assert.equal(await page.locator(".guide-page-book iframe").count(), 1);
  assert.equal(await page.frameLocator(".guide-page-book iframe").locator(".page-book-engine").count(), 1);
  checks.push("六页引导使用同一翻页实例并支持返回与页码更新");
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
  assert.equal(paragraphs.length, 1);
  assert.equal(paragraphs.join("").length, expectedSample.characters);
  checks.push(`匿名样书逐字显示李奶奶开篇${expectedSample.characters}字`);
  stage = "login";
  await visit("/login?next=/admin");
  await page.getByLabel("账号", { exact: true }).fill(admin.username);
  await page.getByLabel("密码", { exact: true }).fill(admin.password);
  await page.getByRole("button", { name: "翻开我的回忆录" }).click();
  await page.waitForURL(url => url.pathname === "/admin", { timeout: 90000 });
  checks.push("公网浏览器真实管理员登录");
  stage = "private_book";
  await visit(`/profiles/${migration.cloud_profile_id}/book`);
  await page.getByText("7 章 · 5,366 字", { exact: true }).waitFor({ timeout: 90000 });
  checks.push("管理员家庭完整七章5366字成书可见");
  const readingEvent = page.waitForResponse(response => response.request().method() === "POST" && /\/api\/v1\/chapters\/[^/]+\/read$/.test(response.url()), { timeout: 90000 });
  await visit(`/profiles/${migration.cloud_profile_id}/book?view=read&chapter=0`);
  await page.frameLocator(".reading-page-book iframe").locator('[aria-hidden="false"] .printed-page-body p').first().waitFor({ timeout: 90000 });
  if ((await readingEvent).status() !== 200) throw new Error("个人回忆录阅读事件没有真实保存");
  checks.push("私有回忆录正文能够翻开阅读");
  checks.push("主阅读入口上报真实阅读事件");
  stage = "admin";
  await page.setViewportSize({ width: 1440, height: 1000 });
  await visit("/admin");
  await page.locator('.admin-workspace[data-layout="desktop-admin"]').waitFor();
  await page.getByRole("heading", { name: "体验总览", exact: true }).waitFor();
  await page.locator(".admin-kpi").first().waitFor({ timeout: 90000 });
  const dashboardWidth = await page.locator(".admin-workspace").evaluate(element => element.getBoundingClientRect().width);
  assert(dashboardWidth > 1200, "电脑后台仍受手机容器限制");
  const measured = await context.request.get(origin + "/api/v1/admin/metrics");
  assert.equal(measured.status(), 200);
  const actual = await measured.json();
  assert.equal(Number(await page.locator(".admin-kpi").first().locator("strong").innerText()), actual.journey.summary.visitors);
  checks.push("电脑后台核心人数与真实管理接口一致");
  await page.locator(".admin-diagnostics summary").click();
  await page.getByLabel("包含管理员的产品体验", { exact: true }).check();
  await page.locator('[data-admin-chart="trend"]').waitFor({ timeout: 90000 });
  assert(await page.getByRole("img", { name: "每日到访与开始访谈人数趋势" }).count() > 0);
  await page.getByRole("button", { name: /^体验转化/ }).click();
  await page.locator(".admin-sequence li").first().waitFor({ timeout: 90000 });
  assert.equal(await page.locator(".admin-sequence li").count(), 13);
  assert.equal(await page.locator(".admin-arrivals li").count(), 7);
  checks.push("实际阶段到达与完整引导路径分别显示");
  await page.getByRole("button", { name: /^页面流失/ }).click();
  await page.getByRole("heading", { name: "所有页面明细", exact: true }).waitFor();
  await page.locator(".admin-table-wrap tbody tr").first().waitFor();
  assert(await page.getByText("品牌首页", { exact: true }).count() > 0);
  assert(await page.getByText("李奶奶样书", { exact: true }).count() > 0);
  checks.push("实际引导及样书访问显示在页面退出明细");
  for (const width of [1024, 1280, 1440, 1920]) {
    await page.setViewportSize({ width, height: 1000 });
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
    if (overflow) throw new Error(`后台页面${width}px出现横向溢出`);
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.screenshot({ path: path.join(evidenceDirectory, "管理页面退出-1440px.png"), fullPage: true, animations: "disabled" });
  await page.getByRole("button", { name: /^体验总览/ }).click();
  await page.screenshot({ path: path.join(evidenceDirectory, "管理总览-1440px.png"), fullPage: true, animations: "disabled" });
  checks.push("电脑后台四种网页宽度正常且图表可用");
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
