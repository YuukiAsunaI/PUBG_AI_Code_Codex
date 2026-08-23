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

async function selectPlayerForForm(page, formSelector, playerName = "Yuuki_Asuna---") {
  const form = page.locator(formSelector);
  const target = form.locator('[name="target"]');
  await target.fill(playerName);
  await target.dispatchEvent("change");
  await page.waitForFunction(
    ({ selector, name }) => {
      const input = document.querySelector(`${selector} [name="target"]`);
      return input?.value === name && Boolean(input.dataset.accountId);
    },
    { selector: formSelector, name: playerName },
    { timeout: 30000 },
  );
  return {
    form,
    accountId: await target.getAttribute("data-account-id"),
    shard: await form.locator('[name="shard"]').inputValue(),
  };
}

async function openWorkspaceSection(page, view, section) {
  await page.locator(`[data-view-target="${view}"]`).click();
  await page.locator(`[data-workspace-section="${section}"]`).click();
}

async function runExpandedFeatureChecks(page) {
  await openWorkspaceSection(page, "settings", "display");
  const displayForm = page.locator("#displaySettingsForm");
  await displayForm.locator('[name="number_format"]').selectOption("korean_units");
  const koreanPreview = await page.locator("#displayNumberPreview").innerText();
  await displayForm.locator('button[type="submit"]').click();
  await page.locator("#displaySettingsStatus").filter({ hasText: "적용 완료" }).waitFor();
  await displayForm.locator('[name="number_format"]').selectOption("grouped");
  await displayForm.locator('button[type="submit"]').click();

  await openWorkspaceSection(page, "players", "matches");

  const matchSelection = await selectPlayerForForm(page, "#matchForm");
  await page.waitForFunction(() => (
    [...document.querySelectorAll('#matchForm select[name="match_id"] option')]
      .some((option) => option.value)
  ));
  const matchForm = matchSelection.form;
  const matchId = await matchForm.locator('select[name="match_id"] option').evaluateAll((options) => (
    options.find((option) => option.value)?.value || ""
  ));
  await matchForm.locator('select[name="match_id"]').selectOption(matchId);
  await matchForm.locator('button[type="submit"]').click();
  await page.locator("#matchBody .result-shell").waitFor({ timeout: 30000 });
  const matchText = await page.locator("#matchBody").innerText();
  const matchApi = await page.evaluate(async ({ id, accountId, shard }) => {
    const params = new URLSearchParams({ match_id: id, account_id: accountId, shard });
    const response = await fetch(`/players/match?${params.toString()}`);
    if (!response.ok) throw new Error(`match API ${response.status}`);
    return (await response.json()).match;
  }, { id: matchId, accountId: matchSelection.accountId, shard: matchSelection.shard });
  const matchScreenshot = path.join(outputDir, "match-analysis-desktop.png");
  await page.locator("#match-lookup").scrollIntoViewIfNeeded();
  await page.screenshot({ path: matchScreenshot, fullPage: false });

  await openWorkspaceSection(page, "players", "landing");
  const dropSelection = await selectPlayerForForm(page, "#dropZoneForm");
  await dropSelection.form.locator('button[type="submit"]').click();
  await page.locator("#dropZoneBody .result-shell").waitFor({ timeout: 30000 });
  await page.waitForFunction(() => {
    const image = document.querySelector("#dropMapImage");
    return Boolean(image?.complete && image.naturalWidth > 0 && image.naturalHeight > 0);
  });
  const markerCount = await page.locator("#dropMapMarkers [data-drop-map-marker]").count();
  if (markerCount > 0) await page.locator("#dropMapMarkers [data-drop-map-marker]").first().click();
  const mapInfo = await page.locator("#dropMapInfo").innerText();
  const landingScreenshot = path.join(outputDir, "landing-analysis-desktop.png");
  await page.locator("#landing-analysis").scrollIntoViewIfNeeded();
  await page.screenshot({ path: landingScreenshot, fullPage: false });
  await page.locator('[data-drop-analysis-view-button="chart"]').click();
  const landingChartRows = await page.locator('#dropZoneBody [data-drop-analysis-view="chart"] .metric-chart-row').count();
  await page.locator('[data-drop-analysis-view-button="table"]').click();
  const landingTableRows = await page.locator("#dropZoneBody .drop-region-table tbody tr").count();

  await openWorkspaceSection(page, "players", "ranking");
  const rankingForm = page.locator("#rankingForm");
  await rankingForm.locator('button[type="submit"]').click();
  await page.locator("#rankingBody .result-shell").waitFor({ timeout: 30000 });
  const rankingTableRows = await page.locator("#rankingBody .detail-table tbody tr").count();
  const rankingGuildOptions = await page.locator("#rankingGuildSelect option").count();
  await page.locator('[data-ranking-view="chart"]').click();
  const rankingChartRows = await page.locator("#rankingBody .metric-chart-row").count();

  await openWorkspaceSection(page, "players", "compare");
  const comparisonForm = page.locator("#comparisonForm");
  await comparisonForm.locator('[name="comparison_type"]').selectOption("player");
  await page.waitForFunction(() => document.querySelectorAll('#comparisonItemPicker input[name="comparison_item"]').length >= 2);
  let selectedComparisons = await page.locator('#comparisonItemPicker input[name="comparison_item"]:checked').count();
  while (selectedComparisons < 2) {
    await page.locator('#comparisonItemPicker input[name="comparison_item"]:not(:checked):not(:disabled)').first().check();
    selectedComparisons += 1;
  }
  await comparisonForm.locator('button[type="submit"]').click();
  await page.locator("#comparisonBody .comparison-bar-row").first().waitFor({ timeout: 60000 });
  const comparisonBars = await page.locator("#comparisonBody .comparison-bar-row").count();
  await page.locator('[data-comparison-view="trend"]').click();
  await page.locator("#comparisonBody .trend-line-chart").waitFor({ timeout: 10000 });
  const comparisonSeries = await page.locator("#comparisonBody .trend-line-chart path").count();
  const comparisonScreenshot = path.join(outputDir, "comparison-analysis-desktop.png");
  await page.locator("#comparison-analysis").scrollIntoViewIfNeeded();
  await page.screenshot({ path: comparisonScreenshot, fullPage: false });

  const items = matchApi.items || [];
  return {
    numberFormat: {
      koreanPreview,
      koreanUnitsRendered: koreanPreview.includes("5만") && koreanPreview.includes("9,452"),
    },
    match: {
      matchId,
      itemRows: items.length,
      hasSummary: ["전체 / 사람 / 봇", "교전 종합", "아이템 종합"].every((label) => matchText.includes(label)),
      usedQuantityMatchesEvents: Number(matchApi.item_summary?.used_quantity || 0) === Number(matchApi.item_summary?.used_events || 0),
      rawItemLabels: items.filter((item) => String(item.item_name || "").startsWith("Item_")).length,
    },
    landing: {
      markerCount,
      mapInfo,
      mapSelected: markerCount > 0 && mapInfo.includes("착지"),
      chartRows: landingChartRows,
      tableRows: landingTableRows,
    },
    ranking: {
      guildOptions: rankingGuildOptions,
      tableRows: rankingTableRows,
      chartRows: rankingChartRows,
    },
    comparison: {
      selected: selectedComparisons,
      bars: comparisonBars,
      trendSeries: comparisonSeries,
    },
    screenshots: { matchScreenshot, landingScreenshot, comparisonScreenshot },
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
    const features = await runExpandedFeatureChecks(desktopPage);
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
        features,
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
      screenshots: { playerScreenshot, auditScreenshot, mobileScreenshot, ...features.screenshots },
    };
    console.log(JSON.stringify(result, null, 2));
    const failed = [
      result.desktop.status !== 200,
      result.mobile.status !== 200,
      !result.desktop.player.hasPlayer,
      !result.mobile.player.hasPlayer,
      !result.desktop.features.numberFormat.koreanUnitsRendered,
      !result.desktop.features.match.hasSummary,
      !result.desktop.features.match.usedQuantityMatchesEvents,
      result.desktop.features.match.rawItemLabels > 0,
      !result.desktop.features.landing.mapSelected,
      result.desktop.features.landing.chartRows < 1,
      result.desktop.features.landing.tableRows < 1,
      result.desktop.features.ranking.guildOptions < 1,
      result.desktop.features.ranking.tableRows < 1,
      result.desktop.features.ranking.chartRows < 1,
      result.desktop.features.comparison.selected < 2,
      result.desktop.features.comparison.bars < 2,
      result.desktop.features.comparison.trendSeries < 2,
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
