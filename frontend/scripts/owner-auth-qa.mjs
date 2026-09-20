import { chromium } from "playwright";

const code = process.env.OWNER_QA_CODE;
const targetUrl = process.env.DOTA2_QA_URL || "https://ashfury.cn/dota2-preview/";
if (!code) throw new Error("OWNER_QA_CODE is required");

const browser = await chromium.launch({
  headless: true,
  executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
});

try {
  const context = await browser.newContext({ viewport: { width: 1585, height: 1024 } });
  const page = await context.newPage();
  const failures = [];
  page.on("response", (response) => {
    if (response.status() >= 400) failures.push(`${response.status()} ${response.url()}`);
  });
  await page.goto(`${targetUrl}?owner=authorize`, { waitUntil: "domcontentloaded", timeout: 20000 });
  await page.getByLabel("一次性授权码").fill(code);
  await page.getByRole("button", { name: "授权设备", exact: true }).click();
  await page.getByText("设备授权成功，深度复盘功能已启用", { exact: true }).waitFor();
  await page.getByRole("button", { name: "深度复盘", exact: true }).waitFor();
  const cookies = await context.cookies("https://ashfury.cn/dota2/");
  const cookie = cookies.find((item) => item.name === "ashfury_owner_session");
  if (!cookie || !cookie.secure || !cookie.httpOnly || cookie.sameSite !== "Strict" || cookie.path !== "/dota2") {
    throw new Error(`Owner cookie attributes are invalid: ${JSON.stringify(cookie)}`);
  }
  await page.screenshot({ path: new URL("../qa/implementation-owner-button.png", import.meta.url).pathname, fullPage: false });
  let revoked;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    revoked = await context.request.delete("https://ashfury.cn/dota2/api/owner-session", {
      headers: { Origin: "https://ashfury.cn" },
    });
    if (revoked.ok()) break;
    if (revoked.status() !== 503) break;
    await new Promise((resolve) => setTimeout(resolve, 2500));
  }
  if (!revoked?.ok()) throw new Error(`Owner session revoke failed: ${revoked?.status()}`);
  if (failures.length) throw new Error(`Browser failures: ${failures.join(", ")}`);
  console.log(JSON.stringify({ ownerButton: true, secureCookie: true, sessionRevoked: true }, null, 2));
} finally {
  await browser.close();
}
