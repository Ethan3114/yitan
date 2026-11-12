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

  const admin = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const dashboard = await admin.newPage();
  dashboard.on("pageerror", error => errors.push(error.name));
  await dashboard.goto(`${origin}/login`);
  await dashboard.getByLabel("账号", { exact: true }).fill("verification-admin");
  await dashboard.getByLabel("密码", { exact: true }).fill(process.env.CHECK_ADMIN_PASSWORD);
  await dashboard.getByRole("button", { name: "翻开我的回忆录" }).click();
  await dashboard.waitForURL(origin + "/");
  await dashboard.goto(`${origin}/admin`);
  await dashboard.getByRole("heading", { name: "体验总览", exact: true }).waitFor();
  await dashboard.locator(".admin-kpi").first().waitFor();
  assert(await dashboard.locator(".admin-workspace").evaluate(element => element.getBoundingClientRect().width) > 1200);
  await dashboard.getByRole("button", { name: /^体验转化/ }).click();
  await dashboard.getByRole("heading", { name: "体验阶段到达", exact: true }).waitFor();
  assert.equal(await dashboard.locator(".admin-sequence li").count(), 13);
  await dashboard.locator(".admin-sequence li button").first().click();
  await dashboard.getByText("上一步 1 人，本步 1 人", { exact: false }).waitFor();
  await dashboard.getByRole("button", { name: /^回访留存/ }).click();
  assert.equal(await dashboard.locator(".admin-retention-bars strong").filter({ hasText: "待观察" }).count(), 3);
  for (const width of [1024, 1440, 1920]) {
    await dashboard.setViewportSize({ width, height: 1000 });
    const bounds = await dashboard.evaluate(() => ({ viewport: innerWidth, width: document.documentElement.scrollWidth }));
    assert(bounds.width <= bounds.viewport + 1, `后台页面在 ${width}px 出现横向溢出`);
  }
  await dashboard.getByRole("button", { name: /^账户与邀请/ }).click();
  await dashboard.getByRole("heading", { name: "家庭账户", exact: true }).waitFor();
  await dashboard.getByText("browser-verification-family", { exact: true }).waitFor();
  assert.deepEqual(errors, []);
  await admin.close();
  console.log("真实浏览器注册登录、权限、电脑后台转化留存与账户管理检查通过");
} finally {
  await browser.close();
}
