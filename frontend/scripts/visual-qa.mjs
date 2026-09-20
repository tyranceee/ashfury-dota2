import { chromium } from "playwright";
import { mkdir } from "node:fs/promises";

const root = new URL("..", import.meta.url).pathname;
const outputDir = `${root}qa`;
const targetUrl = process.env.DOTA2_QA_URL || "https://ashfury.cn/dota2-preview/";
await mkdir(outputDir, { recursive: true });

const browser = await chromium.launch({
  headless: true,
  executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
});

const report = { consoleErrors: [], checks: {}, screenshots: {} };

try {
  const context = await browser.newContext({
    viewport: { width: 1585, height: 1024 },
    deviceScaleFactor: 1,
  });
  const page = await context.newPage();
  page.setDefaultTimeout(12000);
  page.on("console", (message) => {
    if (message.type() === "error") report.consoleErrors.push(message.text());
  });
  page.on("pageerror", (error) => report.consoleErrors.push(error.message));
  page.on("response", (response) => {
    if (response.status() >= 400) report.consoleErrors.push(`${response.status()} ${response.url()}`);
  });

  await page.goto(targetUrl, { waitUntil: "domcontentloaded", timeout: 20000 });
  await page.getByText("赛前历史成分", { exact: true }).waitFor();
  await page.waitForFunction(() => [...document.images].every((img) => img.complete));

  report.checks.playerCards = await page.locator(".historical-profile-card").count();
  report.checks.selfProfile = await page.getByText("本人追踪", { exact: true }).isVisible();
  report.checks.realLatestMatch = await page.getByText("9001763544", { exact: true }).count();
  report.checks.accountIds = await page.locator(".account-id").count();
  report.checks.profileSummaries = await page.locator(".historical-profile-card p").count();
  report.checks.ownerButtonHiddenForVisitor = await page.getByRole("button", { name: "深度复盘", exact: true }).count() === 0;

  const desktopPath = `${outputDir}/implementation-desktop.png`;
  await page.screenshot({ path: desktopPath, fullPage: false });
  report.screenshots.desktop = desktopPath;

  await page.locator(".player-card").first().click();
  await page.getByRole("dialog").waitFor();
  report.checks.playerDialog = await page.getByRole("dialog").isVisible();
  const dialogPath = `${outputDir}/implementation-player-dialog.png`;
  await page.screenshot({ path: dialogPath, fullPage: false });
  report.screenshots.playerDialog = dialogPath;
  await page.getByRole("button", { name: "关闭" }).click();

  await page.getByRole("button", { name: "比赛", exact: true }).click();
  report.checks.matchLibrary = await page.getByText("最近比赛", { exact: true }).isVisible();
  await page.getByRole("button", { name: "总览", exact: true }).click();

  await page.getByRole("button", { name: "数据/API", exact: true }).click();
  await page.getByText("数据与文件", { exact: true }).waitFor();
  await page.waitForFunction(() => document.querySelector(".data-files-card dd")?.textContent.trim() !== "—");
  report.checks.artifactInventory = await page.getByText("暂无本地附件", { exact: true }).isVisible();
  report.checks.unifiedParseSource = await page.getByText("OpenDota", { exact: true }).isVisible();
  const dataApiPath = `${outputDir}/implementation-data-api.png`;
  await page.screenshot({ path: dataApiPath, fullPage: false });
  report.screenshots.dataApi = dataApiPath;
  await page.getByRole("button", { name: "总览", exact: true }).click();

  const search = page.getByPlaceholder("输入 Match ID");
  await search.fill("9000265617");
  await search.press("Enter");
  await page.getByText("已切换到比赛 9000265617", { exact: true }).waitFor();
  report.checks.matchSearch = await page.getByText("9000265617", { exact: true }).count();

  await context.close();
  await new Promise((resolve) => setTimeout(resolve, 6000));

  const ownerContext = await browser.newContext({ viewport: { width: 900, height: 700 }, deviceScaleFactor: 1 });
  const ownerPage = await ownerContext.newPage();
  await ownerPage.goto(`${targetUrl}?owner=authorize`, { waitUntil: "domcontentloaded", timeout: 20000 });
  report.checks.ownerAuthorize = await ownerPage.getByRole("dialog", { name: "Owner 设备授权" }).isVisible();
  const ownerAuthorizePath = `${outputDir}/implementation-owner-authorize.png`;
  await ownerPage.screenshot({ path: ownerAuthorizePath, fullPage: false });
  report.screenshots.ownerAuthorize = ownerAuthorizePath;
  await ownerContext.close();
  await new Promise((resolve) => setTimeout(resolve, 6000));

  const mobileContext = await browser.newContext({
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 1,
  });
  const mobilePage = await mobileContext.newPage();
  mobilePage.setDefaultTimeout(12000);
  mobilePage.on("console", (message) => {
    if (message.type() === "error") report.consoleErrors.push(`mobile: ${message.text()}`);
  });
  mobilePage.on("pageerror", (error) => report.consoleErrors.push(`mobile: ${error.message}`));
  mobilePage.on("response", (response) => {
    if (response.status() >= 400) report.consoleErrors.push(`mobile: ${response.status()} ${response.url()}`);
  });
  await mobilePage.goto(targetUrl, { waitUntil: "domcontentloaded", timeout: 20000 });
  await mobilePage.getByText("赛前历史成分", { exact: true }).waitFor();
  await mobilePage.waitForFunction(() => [...document.images].every((img) => img.complete));
  const mobilePath = `${outputDir}/implementation-mobile.png`;
  await mobilePage.screenshot({ path: mobilePath, fullPage: false });
  report.screenshots.mobile = mobilePath;
  report.checks.mobileNav = await mobilePage.getByRole("button", { name: "总览", exact: true }).isVisible();
  report.checks.mobileOverflow = await mobilePage.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
  await mobileContext.close();
} finally {
  await browser.close();
}

console.log(JSON.stringify(report, null, 2));
