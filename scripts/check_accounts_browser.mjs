import { chromium } from "../frontend/node_modules/@playwright/test/index.mjs";
import assert from "node:assert/strict";

const browser = await chromium.launch({ channel: "chrome", headless: true });
const origin = process.env.CHECK_ORIGIN;
assert(origin && process.env.CHECK_ADMIN_PASSWORD && process.env.CHECK_INVITATION && process.env.CHECK_PASSWORD);
try {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.name));
  await page.goto(`${origin}/register`);
  await page.getByLabel("邀请码", { exact: true }).fill(process.env.CHECK_INVITATION);
  await page.getByLabel("账号", { exact: true }).fill("browser-verification-family");
  await page.getByLabel("密码", { exact: true }).fill(process.env.CHECK_PASSWORD);
  await page.getByLabel("再次填写密码").fill(process.env.CHECK_PASSWORD);
  await page.getByRole("button", { name: "注册并开始记录" }).click();
  await page.waitForURL(/profiles\?new=1/);
  await page.goto(`${origin}/admin`);
  await page.getByText("仅管理员可以查看运营数据", { exact: true }).waitFor();
  assert.equal(await page.getByRole("button", { name: "创建邀请码", exact: true }).count(), 0);
  await context.close();

  const admin = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const dashboard = await admin.newPage();
  dashboard.on("pageerror", error => errors.push(error.name));
  await dashboard.goto(`${origin}/login`);
  await dashboard.getByLabel("账号", { exact: true }).fill("verification-admin");
  await dashboard.getByLabel("密码", { exact: true }).fill(process.env.CHECK_ADMIN_PASSWORD);
  await dashboard.getByRole("button", { name: "翻开我的回忆录" }).click();
  await dashboard.waitForURL(origin + "/");
  await dashboard.goto(`${origin}/admin`);
  await dashboard.getByRole("heading", { name: "找到用户停下的位置" }).waitFor();
  await dashboard.getByRole("button", { name: "分阶段转化", exact: true }).click();
  await dashboard.getByRole("heading", { name: "从第一次翻书，到读到自己的故事" }).waitFor();
  assert.equal(await dashboard.locator(".admin-funnel li").count(), 13);
  await dashboard.locator(".admin-funnel li button").first().click();
  await dashboard.getByText("进入上一步 1，完成本步 1。", { exact: false }).waitFor();
  await dashboard.getByRole("button", { name: "用户留存", exact: true }).click();
  assert.equal(await dashboard.getByRole("cell", { name: "待观察", exact: true }).count(), 3);
  for (const width of [320, 390, 430]) {
    await dashboard.setViewportSize({ width, height: 844 });
    const bounds = await dashboard.evaluate(() => ({ viewport: innerWidth, width: document.documentElement.scrollWidth }));
    assert(bounds.width <= bounds.viewport + 1, `后台页面在 ${width}px 出现横向溢出`);
  }
  await dashboard.getByRole("button", { name: "账户邀请", exact: true }).click();
  await dashboard.getByRole("heading", { name: "家庭账户", exact: true }).waitFor();
  await dashboard.getByText("browser-verification-family", { exact: true }).waitFor();
  assert.deepEqual(errors, []);
  await admin.close();
  console.log("手机浏览器注册、登录、权限、漏斗、留存、账户及三种宽度检查通过");
} finally {
  await browser.close();
}
