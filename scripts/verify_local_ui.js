const fs = require("fs");
const os = require("os");
const path = require("path");
const { chromium } = require("playwright");

const baseUrl = process.argv[2] || "http://127.0.0.1:8770";
const outputDir = path.join(os.tmpdir(), "pubg-ai-browser-qa");
fs.mkdirSync(outputDir, { recursive: true });

function installedBrowserPath() {
  const candidates = [
    "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
    "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
    "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
  ];
  return candidates.find((candidate) => fs.existsSync(candidate));
}

function monitorPage(page) {
  const consoleErrors = [];
  const requestFailures = [];
  const httpErrors = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("pageerror", (error) => consoleErrors.push(`PAGEERROR: ${error.message}`));
  page.on("requestfailed", (request) => {
    requestFailures.push(`${request.url()} :: ${request.failure()?.errorText || "failed"}`);
  });
  page.on("response", (response) => {
    if (response.status() >= 400) httpErrors.push(`${response.status()} ${response.url()}`);
  });
  return { consoleErrors, requestFailures, httpErrors };
}

async function openManager(page) {
  const response = await page.goto(baseUrl, { waitUntil: "networkidle", timeout: 30000 });
  await page.waitForSelector('[data-view-target="players"]');
  await page.waitForFunction(() => document.querySelectorAll("#registeredPlayerOptions option").length > 0);
  return response;
}

async function runPlayerAnalysis(page) {
  await page.locator('[data-view-target="players"]').click();
  const form = page.locator("#intelligenceForm");
  try {
    const target = form.locator('[name="target"]');
    await target.fill("Yuuki_Asuna---");
    await target.dispatchEvent("change");
    await page.locator("#analysisPlayerContextName").filter({ hasText: "Yuuki_Asuna---" }).waitFor({ timeout: 30000 });
    await form.locator('button[type="submit"]').click();
    await page.locator("#intelligenceBody .intelligence-quality-strip").waitFor({ timeout: 60000 });
  } catch (error) {
    const diagnostics = await page.evaluate(() => ({
      banner: document.querySelector("#banner")?.textContent?.trim() || "",
      context: document.querySelector("#analysisPlayerContextName")?.textContent?.trim() || "",
      body: document.querySelector("#intelligenceBody")?.textContent?.trim().slice(0, 1000) || "",
    }));
    const failureScreenshot = path.join(outputDir, `player-analysis-failure-${Date.now()}.png`);
    await page.screenshot({ path: failureScreenshot, fullPage: false });
    throw new Error(`${error.message}\nDiagnostics: ${JSON.stringify(diagnostics)}\nScreenshot: ${failureScreenshot}`);
  }
  const quality = await page.locator("#intelligenceBody .intelligence-quality-strip").innerText();
  const overview = await page.locator("#intelligenceBody").innerText();
  await page.locator('[data-intelligence-view="trends"]').click();
  await page.locator("#intelligenceBody svg").first().waitFor({ timeout: 10000 });
  const chartCount = await page.locator("#intelligenceBody svg").count();
  await page.locator('[data-intelligence-view="evidence"]').click();
  await page.locator("#intelligenceBody .intelligence-definition").first().waitFor();
  const definitionCount = await page.locator("#intelligenceBody .intelligence-definition").count();
  return {
    quality,
    hasPlayer: overview.includes("328") && overview.includes("명중률"),
    chartCount,
    definitionCount,
  };
}

async function runDataQualityAudit(page) {
  await page.locator('[data-view-target="operations"]').click();
  await page.locator("#playerIntelligenceAuditRun").click();
  await page.locator("#playerIntelligenceAuditStatus").filter({ hasText: "전체 통과" }).waitFor({ timeout: 30000 });
  return {
    status: await page.locator("#playerIntelligenceAuditStatus").innerText(),
    checkRows: await page.locator("#playerIntelligenceAuditBody .data-quality-checks tbody tr").count(),
    failedRows: await page.locator("#playerIntelligenceAuditBody .alert-severity-error").count(),
  };
}

async function layoutDiagnostics(page) {
  return page.evaluate(() => ({
    viewportWidth: window.innerWidth,
    documentWidth: document.documentElement.scrollWidth,
    blank: document.body.innerText.trim().length === 0,
    overlay: Boolean(document.querySelector("[data-nextjs-dialog], .vite-error-overlay, #webpack-dev-server-client-overlay")),
    overflowingButtons: Array.from(document.querySelectorAll("button"))
      .filter((button) => button.offsetParent !== null)
      .filter((button) => button.scrollWidth > button.clientWidth + 1 || button.scrollHeight > button.clientHeight + 1)
      .map((button) => button.textContent.trim())
      .slice(0, 20),
  }));
}

(async () => {
  const executablePath = installedBrowserPath();
  if (!executablePath) throw new Error("Microsoft Edge or Google Chrome is required for UI verification.");
  const browser = await chromium.launch({ headless: true, executablePath });
  try {
    const desktopContext = await browser.newContext({
      viewport: { width: 1536, height: 900 },
      locale: "ko-KR",
      timezoneId: "Asia/Seoul",
    });
    const desktopPage = await desktopContext.newPage();
    const desktopMonitor = monitorPage(desktopPage);
    const desktopResponse = await openManager(desktopPage);
    const player = await runPlayerAnalysis(desktopPage);
    const playerScreenshot = path.join(outputDir, "player-intelligence-desktop.png");
    await desktopPage.screenshot({ path: playerScreenshot, fullPage: false });
    const audit = await runDataQualityAudit(desktopPage);
    const auditScreenshot = path.join(outputDir, "data-quality-desktop.png");
    await desktopPage.screenshot({ path: auditScreenshot, fullPage: false });
    const desktopLayout = await layoutDiagnostics(desktopPage);
    await desktopContext.close();

    const mobileContext = await browser.newContext({
      viewport: { width: 390, height: 844 },
      isMobile: true,
      locale: "ko-KR",
      timezoneId: "Asia/Seoul",
    });
    const mobilePage = await mobileContext.newPage();
    const mobileMonitor = monitorPage(mobilePage);
    const mobileResponse = await openManager(mobilePage);
    const mobilePlayer = await runPlayerAnalysis(mobilePage);
    const mobileScreenshot = path.join(outputDir, "player-intelligence-mobile.png");
    await mobilePage.screenshot({ path: mobileScreenshot, fullPage: false });
    const mobileLayout = await layoutDiagnostics(mobilePage);
    await mobileContext.close();

    const result = {
      baseUrl,
      desktop: {
        status: desktopResponse?.status(),
        player,
        audit,
        layout: desktopLayout,
        ...desktopMonitor,
      },
      mobile: {
        status: mobileResponse?.status(),
        player: mobilePlayer,
        layout: mobileLayout,
        ...mobileMonitor,
      },
      screenshots: { playerScreenshot, auditScreenshot, mobileScreenshot },
    };
    console.log(JSON.stringify(result, null, 2));
    const failed = [
      result.desktop.status !== 200,
      result.mobile.status !== 200,
      !result.desktop.player.hasPlayer,
      !result.mobile.player.hasPlayer,
      result.desktop.audit.failedRows > 0,
      result.desktop.consoleErrors.length > 0,
      result.mobile.consoleErrors.length > 0,
      result.desktop.requestFailures.length > 0,
      result.mobile.requestFailures.length > 0,
      result.desktop.layout.blank || result.mobile.layout.blank,
      result.desktop.layout.overlay || result.mobile.layout.overlay,
      result.desktop.layout.documentWidth > result.desktop.layout.viewportWidth + 1,
      result.mobile.layout.documentWidth > result.mobile.layout.viewportWidth + 1,
      result.desktop.layout.overflowingButtons.length > 0,
      result.mobile.layout.overflowingButtons.length > 0,
    ].some(Boolean);
    process.exitCode = failed ? 1 : 0;
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
