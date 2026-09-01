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
  await openWorkspaceSection(page, "players", "intelligence");
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
  await page.locator('[data-intelligence-view="tactics"]').click();
  await page.locator("#intelligenceBody").filter({ hasText: "고급 판단 분석" }).waitFor({ timeout: 30000 });
  const tacticsText = await page.locator("#intelligenceBody").innerText();
  const tacticsSections = await page.locator("#intelligenceBody .intelligence-data-section").count();
  const unconvertedLargeNumbers = [...overview.matchAll(/\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b/g)]
    .map((match) => match[0])
    .filter((text) => Number(text.replaceAll(",", "")) >= 10000);
  return {
    quality,
    hasPlayer: overview.includes("명중률") && overview.includes("교전"),
    chartCount,
    definitionCount,
    tacticsSections,
    tacticsReady: [
      "교전 의사결정",
      "자기장과 로테이션",
      "팀 협동",
      "파밍 준비",
      "최근 변화 신호",
      "최근 패배 교전 복기",
    ].every((label) => tacticsText.includes(label)),
    tacticsBackfillWaiting: tacticsText.includes("고급 판단 분석 백필 대기"),
    hasKoreanLargeUnit: /\d+(?:억|만)(?:\s|\d)/.test(overview),
    unconvertedLargeNumbers: [...new Set(unconvertedLargeNumbers)].slice(0, 20),
  };
}

async function runRecommendationChecks(page) {
  await openWorkspaceSection(page, "players", "recommendations");
  const selection = await selectPlayerForForm(page, "#recommendationForm");
  const form = selection.form;
  const details = form.locator("details.advanced-filters");
  await details.locator("summary").click();
  const filterFields = await details.locator("[name]").evaluateAll((items) => (
    items.map((item) => item.getAttribute("name")).filter(Boolean)
  ));
  await page.waitForFunction(() => (
    [...document.querySelectorAll('#recommendationForm select[name="map_name"] option')]
      .some((option) => option.value)
  ));
  const mapSelect = form.locator('select[name="map_name"]');
  const selectedMap = await mapSelect.locator("option").evaluateAll((options) => {
    const option = options.find((item) => item.value);
    return option ? { value: option.value, label: option.textContent.trim() } : { value: "", label: "" };
  });
  await mapSelect.selectOption(selectedMap.value);
  await form.locator('[name="min_matches"]').fill("3");
  await form.locator('button[type="submit"]').click();
  await page.locator("#recommendationBody .result-shell").waitFor({ timeout: 60000 });

  const summaryText = await page.locator("#recommendationBody").innerText();
  const weaponSummary = page.locator("#recommendationBody summary").filter({ hasText: "무기별 상세" }).first();
  if (await weaponSummary.count()) await weaponSummary.click();
  const weaponScoreSummary = page.locator("#recommendationBody summary").filter({ hasText: "무기 점수 계산" }).first();
  if (await weaponScoreSummary.count()) await weaponScoreSummary.click();
  const scoreText = await page.locator("#recommendationBody").innerText();

  await page.locator('[data-recommendation-view="chart"]').click();
  const chartPanel = page.locator('[data-recommendation-panel="chart"]');
  await chartPanel.locator(".metric-chart-row").first().waitFor({ timeout: 30000 });
  const chartRows = await chartPanel.locator(".metric-chart-row").count();
  await chartPanel.locator("[data-recommendation-chart-metric]").selectOption("fight_win_rate");
  const chartText = await chartPanel.innerText();
  const screenshot = path.join(outputDir, "recommendation-confidence-desktop.png");
  await page.locator("#recommendation-lookup").scrollIntoViewIfNeeded();
  await page.screenshot({ path: screenshot, fullPage: false });

  return {
    filterFields,
    hasDetailedFilters: [
      "map_name", "game_mode", "team_mode", "perspective", "match_type", "season_state",
      "year", "quarter", "month", "exact_date_kst", "hour", "from_date_kst", "to_date_kst",
    ].every((name) => filterFields.includes(name)),
    hasLoadoutRecommendation: summaryText.includes("추천 2주무기 조합"),
    selectedMap,
    appliedMapCondition: Boolean(selectedMap.label) && summaryText.includes(selectedMap.label),
    hasAdjustedRates: scoreText.includes("표본 보정")
      && scoreText.includes("승률 표본 신뢰도")
      && scoreText.includes("최종 점수 반영 계수"),
    chartRows,
    chartMetricApplied: chartText.includes("무기 · 교전 승리 확률"),
    screenshot,
  };
}

async function runDataQualityAudit(page) {
  await page.locator('[data-view-target="operations"]').click();
  await page.locator("#playerIntelligenceAuditRun").click();
  await page.locator("#playerIntelligenceAuditStatus")
    .filter({ hasText: /전체 통과|확인 필요|오류/ })
    .waitFor({ timeout: 60000 });
  const bodyText = await page.locator("#playerIntelligenceAuditBody").innerText();
  return {
    status: await page.locator("#playerIntelligenceAuditStatus").innerText(),
    checkRows: await page.locator("#playerIntelligenceAuditBody .data-quality-checks tbody tr").count(),
    failedRows: await page.locator("#playerIntelligenceAuditBody .alert-severity-error").count(),
    hasScopeAndFreshness: [
      "분석 대상 경기",
      "정책 제외",
      "마지막 분석 경기",
      "마지막 텔레메트리 저장",
      "번역 사전 커버리지",
    ].every((label) => bodyText.includes(label)),
  };
}

async function runWholeMatchReplay(page) {
  await openWorkspaceSection(page, "replay", "player");
  const selection = await selectPlayerForForm(page, "#timelinePlayerForm");
  await selection.form.locator('button[type="submit"]').click();
  await page.waitForFunction(() => {
    const select = document.querySelector("#timelineSelect");
    const status = document.querySelector("#replayPlayerStatus")?.textContent || "";
    return Boolean(
      select
      && !select.disabled
      && [...select.options].some((option) => option.value)
      && /전체\s+[1-9][0-9,]*명/.test(status)
      && !status.includes("준비 중")
    );
  }, null, { timeout: 120000 });
  await page.waitForFunction(() => (
    document.querySelectorAll("#timelineTeamList [data-timeline-actor]").length > 4
  ), null, { timeout: 30000 });

  const status = await page.locator("#replayPlayerStatus").innerText();
  const participantCards = await page.locator("#timelineTeamList [data-timeline-actor]").count();
  const actorCheckboxes = await page.locator("#timelineActorFilter [data-timeline-actor-filter]").count();
  const defaultActorChecked = await page.locator("#timelineActorFilter [data-timeline-actor-filter]:checked").count();
  const eventTypeCheckboxes = await page.locator("#timelineEventTypeFilter [data-timeline-event-type-filter]").count();
  const defaultEventTypeChecked = await page.locator("#timelineEventTypeFilter [data-timeline-event-type-filter]:checked").count();
  const participantText = await page.locator("#timelineTeamList").innerText();
  const checkedLayers = await page.locator([
    "#timelineShowPath:checked",
    "#timelineShowCombat:checked",
    "#timelineShowEngagements:checked",
    "#timelineShowDbno:checked",
    "#timelineShowKills:checked",
    "#timelineShowAllies:checked",
    "#timelineShowEnemies:checked",
    "#timelineShowBots:checked",
  ].join(", ")).count();

  const allSelectionStartedAt = Date.now();
  await page.locator('[data-timeline-actor-action="all"]').click();
  await page.waitForFunction(() => {
    const all = document.querySelectorAll("#timelineActorFilter [data-timeline-actor-filter]").length;
    const checked = document.querySelectorAll("#timelineActorFilter [data-timeline-actor-filter]:checked").length;
    return all > 0 && checked === all;
  });
  const allSelectionRenderMs = Date.now() - allSelectionStartedAt;
  const allActorChecked = await page.locator("#timelineActorFilter [data-timeline-actor-filter]:checked").count();
  const allSelectionEventWindow = await page.locator("#timelineEventList").evaluate((element) => ({
    total: Number(element.dataset.totalCount || 0),
    rendered: Number(element.dataset.renderedCount || 0),
  }));

  const actorSearchName = actorCheckboxes > 1
    ? await page.locator("#timelineActorFilter .replay-checkbox-option strong").nth(1).innerText()
    : await page.locator("#timelineActorFilter .replay-checkbox-option strong").first().innerText();
  await page.locator("#timelineParticipantSearch").fill(actorSearchName);
  await page.waitForTimeout(100);
  const searchedActorCheckboxes = await page.locator("#timelineActorFilter [data-timeline-actor-filter]").count();
  await page.locator("#timelineParticipantSearch").fill("");
  await page.waitForTimeout(100);
  const restoredActorCheckboxes = await page.locator("#timelineActorFilter [data-timeline-actor-filter]").count();
  const restoredActorChecked = await page.locator("#timelineActorFilter [data-timeline-actor-filter]:checked").count();

  await page.locator('[data-timeline-type-action="none"]').click();
  await page.waitForTimeout(100);
  const typeNoneCount = await page.locator("#timelineEventList [data-timeline-event-item]").count();
  await page.locator('[data-timeline-event-type-filter][value="dbno"]').check();
  await page.locator('[data-timeline-event-type-filter][value="kill"]').check();
  await page.waitForTimeout(200);
  const multiTypeEventCount = await page.locator("#timelineEventList [data-timeline-event-item]").count();
  const filteredEventTypes = await page.locator("#timelineEventList [data-timeline-event-item]").evaluateAll((items) => (
    [...new Set(items.flatMap((item) => (item.dataset.timelineEventType || "").split(/\s+/).filter(Boolean)))].sort()
  ));
  await page.locator('[data-timeline-type-action="all"]').click();
  await page.waitForTimeout(200);
  const eventCount = await page.locator("#timelineEventList [data-timeline-event-item]").count();
  const eventText = await page.locator("#timelineEventList").innerText();
  const canvas = await page.locator("#replayCanvas").evaluate((element) => {
    const context = element.getContext("2d");
    const pixels = context.getImageData(0, 0, element.width, element.height).data;
    let opaque = 0;
    const colors = new Set();
    for (let index = 0; index < pixels.length; index += 1600) {
      if (pixels[index + 3] > 0) opaque += 1;
      colors.add(`${pixels[index]}:${pixels[index + 1]}:${pixels[index + 2]}:${pixels[index + 3]}`);
    }
    return { opaque, sampledColors: colors.size };
  });

  const before = Number(await page.locator("#timelineScrubber").inputValue());
  await page.locator("#timelineSpeed").selectOption("8");
  await page.locator("#timelinePlayButton").click();
  await page.waitForTimeout(900);
  const after = Number(await page.locator("#timelineScrubber").inputValue());
  if ((await page.locator("#timelinePlayButton").innerText()) !== "재생") {
    await page.locator("#timelinePlayButton").click();
  }

  const screenshot = path.join(outputDir, "whole-match-replay-desktop.png");
  await page.locator("#replay-player").scrollIntoViewIfNeeded();
  await page.screenshot({ path: screenshot, fullPage: false });
  const actorPriorityFixture = await page.evaluate(() => {
    const previousTimeline = activeTimeline;
    const previousFilters = activeTimelineActorFilters;
    const previousSearch = timelineParticipantSearch.value;
    try {
      activeTimeline = {
        player: { account_id: "fixture.focus", name: "Focus" },
        team: {
          members: [
            { account_id: "fixture.other", name: "Other human", team_id: 3 },
            { account_id: "fixture.bot", name: "Bot", team_id: 4, is_ai_or_bot: true },
            { account_id: "fixture.ally-threat", name: "Ally threat", team_id: 2 },
            { account_id: "fixture.ally", name: "Ally", team_id: 1 },
            { account_id: "fixture.focus-both", name: "C Both threat", team_id: 2 },
            { account_id: "fixture.focus-kill", name: "B Kill threat", team_id: 2 },
            { account_id: "fixture.focus-down", name: "A Down threat", team_id: 2 },
            { account_id: "fixture.focus", name: "Focus", team_id: 1, is_self: true },
          ],
        },
        combat_events: [
          {
            action: "dbno_taken",
            actor_account_id: "fixture.focus",
            related_account_id: "fixture.focus-down",
          },
          {
            action: "death",
            actor_account_id: "fixture.focus",
            related_account_id: "fixture.focus-kill",
          },
          {
            action: "dbno_taken",
            actor_account_id: "fixture.focus",
            related_account_id: "fixture.focus-both",
          },
          {
            action: "death",
            actor_account_id: "fixture.focus",
            related_account_id: "fixture.focus-both",
          },
        ],
        team_tracks: [
          {
            account_id: "fixture.ally",
            combat_events: [
              {
                action: "death",
                actor_account_id: "fixture.ally",
                related_account_id: "fixture.ally-threat",
              },
            ],
          },
        ],
      };
      activeTimelineActorFilters = new Set(["focus"]);
      timelineParticipantSearch.value = "";
      renderTimelineActorFilter();
      return [...document.querySelectorAll("#timelineActorFilter [data-timeline-actor-group]")].map((item) => ({
        name: item.querySelector("strong")?.textContent || "",
        group: item.dataset.timelineActorGroup,
        priority: Number(item.dataset.timelineActorPriority),
        label: item.querySelector("small")?.textContent || "",
      }));
    } finally {
      activeTimeline = previousTimeline;
      activeTimelineActorFilters = previousFilters;
      timelineParticipantSearch.value = previousSearch;
      renderTimelineActorFilter();
    }
  });
  return {
    status,
    participantCards,
    actorCheckboxes,
    defaultActorChecked,
    allActorChecked,
    allSelectionRenderMs,
    allSelectionEventWindow,
    searchedActorCheckboxes,
    restoredActorCheckboxes,
    restoredActorChecked,
    eventTypeCheckboxes,
    defaultEventTypeChecked,
    typeNoneCount,
    multiTypeEventCount,
    filteredEventTypes,
    hasEnemy: participantText.includes("적군"),
    hasFocus: participantText.includes("기준 유저"),
    checkedLayers,
    eventCount,
    hasCombatEvent: ["교전", "명중", "피격", "기절", "킬", "사망"].some((label) => eventText.includes(label)),
    canvas,
    playbackAdvanced: after > before,
    actorPriorityFixture,
    screenshot,
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

async function runWorkspaceContentCheck(page) {
  const views = await page.locator("[data-view-target]").evaluateAll((items) => (
    [...new Set(items.map((item) => item.dataset.viewTarget).filter(Boolean))]
  ));
  const checks = [];

  for (const view of views) {
    await page.locator(`[data-view-target="${view}"]`).first().click();
    await page.waitForTimeout(100);
    const tabs = await page.locator("#workspaceSections [data-workspace-section]").evaluateAll(
      (items) => items.map((item) => item.dataset.workspaceSection).filter(Boolean),
    );
    const targets = tabs.length ? tabs : [""];
    for (const tab of targets) {
      if (tab) {
        await page.locator(`#workspaceSections [data-workspace-section="${tab}"]`).click();
        await page.waitForTimeout(100);
      }
      checks.push(await page.evaluate(({ expectedView, expectedTab }) => {
        const allSections = [...document.querySelectorAll(`section[data-view="${expectedView}"]`)];
        const workspaceSections = allSections.filter((section) => section.parentElement?.id === "workspace");
        const visibleSections = workspaceSections.filter((section) => {
          const style = getComputedStyle(section);
          const box = section.getBoundingClientRect();
          return !section.hidden && style.display !== "none" && box.width > 0 && box.height > 0;
        });
        return {
          view: expectedView,
          tab: expectedTab || "default",
          activeView: document.body.dataset.activeView || "",
          activeTab:
            document.querySelector("#workspaceSections [data-workspace-section].active")
              ?.dataset.workspaceSection || "",
          sectionCount: allSections.length,
          workspaceSectionCount: workspaceSections.length,
          visibleSectionIds: visibleSections.map((section) => section.id),
          hasVisibleContent: visibleSections.some((section) => section.innerText.trim().length > 0),
        };
      }, { expectedView: view, expectedTab: tab }));
    }
    if (tabs.length) {
      await page.locator(`#workspaceSections [data-workspace-section="${tabs[0]}"]`).click();
    }
  }
  await page.locator('[data-view-target="overview"]').first().click();

  const failures = checks.filter((check) => (
    check.activeView !== check.view
    || (check.tab !== "default" && check.activeTab !== check.tab)
    || check.sectionCount < 1
    || check.workspaceSectionCount !== check.sectionCount
    || check.visibleSectionIds.length < 1
    || !check.hasVisibleContent
  ));
  return { checked: checks.length, valid: failures.length === 0, failures };
}

async function runRegistryAndDimensionChecks(page) {
  await openWorkspaceSection(page, "players", "registry");
  await page.locator("#playersBody tr").first().waitFor({ timeout: 30000 });
  const registryRows = await page.locator("#playersBody tr").count();
  const managementButtons = await page.locator("#playersBody [data-player-management]").count();
  const discordEditors = await page.locator("#playersBody [data-player-discord-add]").count();
  const discordChips = await page.locator("#playersBody [data-player-discord-remove]").count();
  const registryScreenshot = path.join(outputDir, "player-registry-desktop.png");
  await page.locator("#registered-players").scrollIntoViewIfNeeded();
  await page.screenshot({ path: registryScreenshot, fullPage: false });

  await openWorkspaceSection(page, "players", "trends");
  const selection = await selectPlayerForForm(page, "#trendForm");
  const granularity = selection.form.locator('[name="granularity"]');
  const submit = selection.form.locator('button[type="submit"]');
  const rowsByDimension = {};
  for (const [value, label] of [["weapon", "무기별"], ["map", "맵별"], ["hour", "시간대별"]]) {
    await granularity.selectOption(value);
    await submit.click();
    await page.locator("#trendSummary").filter({ hasText: label }).waitFor({ timeout: 30000 });
    rowsByDimension[value] = await page.locator("#trendBody tr").count();
  }

  await granularity.selectOption("weapon");
  await submit.click();
  await page.locator("#trendSummary").filter({ hasText: "무기별" }).waitFor({ timeout: 30000 });
  const summary = await page.locator("#trendSummary").innerText();
  await page.locator('[data-trend-view="chart"]').click();
  await page.locator("#trendChartMetric").selectOption("kda");
  await page.locator("#trendChartPanel .metric-chart-row").first().waitFor({ timeout: 10000 });
  const weaponChartRows = await page.locator("#trendChartPanel .metric-chart-row").count();
  const trendScreenshot = path.join(outputDir, "dimension-performance-desktop.png");
  await page.locator("#trend-lookup").scrollIntoViewIfNeeded();
  await page.screenshot({ path: trendScreenshot, fullPage: false });

  return {
    registryRows,
    managementButtons,
    discordEditors,
    discordChips,
    rowsByDimension,
    weaponChartRows,
    hasDetailedMetrics: ["치킨", "KDA", "교전 승리 확률"].every((label) => summary.includes(label)),
    screenshots: { registryScreenshot, trendScreenshot },
  };
}

async function runMobileRegistryCheck(page) {
  await openWorkspaceSection(page, "players", "registry");
  await page.locator("#playersBody tr").first().waitFor({ timeout: 30000 });
  const screenshot = path.join(outputDir, "player-registry-mobile.png");
  await page.locator("#registered-players").scrollIntoViewIfNeeded();
  await page.screenshot({ path: screenshot, fullPage: false });
  return {
    rows: await page.locator("#playersBody tr").count(),
    editors: await page.locator("#playersBody [data-player-discord-add]").count(),
    screenshot,
  };
}

async function runWorkspaceNavigationCheck(page) {
  const snapshot = (label) => page.evaluate((label) => {
    const main = document.querySelector("main");
    const side = document.querySelector(".side-panel");
    const header = document.querySelector(".app-header");
    const heading = document.querySelector(".workspace-heading");
    const firstMenu = document.querySelector('[data-view-target="overview"]');
    return {
      label,
      documentTop: document.scrollingElement.scrollTop,
      windowY: window.scrollY,
      mainTop: main.scrollTop,
      sideTop: side.scrollTop,
      headerTop: Math.round(header.getBoundingClientRect().top),
      headingTop: Math.round(heading.getBoundingClientRect().top),
      firstMenuTop: Math.round(firstMenu.getBoundingClientRect().top),
    };
  }, label);

  await page.locator('[data-view-target="operations"]').click();
  await page.waitForTimeout(100);
  const results = [await snapshot("operations")];
  for (const section of ["alerts", "runs"]) {
    await page.locator(`[data-workspace-section="${section}"]`).click();
    await page.waitForTimeout(250);
    results.push(await snapshot(section));
  }
  await page.evaluate(() => window.scrollTo(0, 5000));
  await page.waitForTimeout(100);
  results.push(await snapshot("forced-document-scroll"));
  return {
    results,
    stable: results.every((item) => (
      item.documentTop === 0
      && item.windowY === 0
      && item.mainTop === 0
      && item.sideTop === 0
      && item.headerTop === 0
      && item.headingTop >= 0
      && item.firstMenuTop >= 0
    )),
  };
}

async function runExpandedFeatureChecks(page) {
  await openWorkspaceSection(page, "settings", "display");
  const displayForm = page.locator("#displaySettingsForm");
  const originalNumberMode = await displayForm.locator('[name="number_format"]').inputValue();
  await displayForm.locator('[name="number_format"]').selectOption("korean_units");
  const koreanPreview = await page.locator("#displayNumberPreview").innerText();
  await Promise.all([
    page.waitForNavigation({ waitUntil: "networkidle" }),
    displayForm.locator('button[type="submit"]').click(),
  ]);
  await openWorkspaceSection(page, "settings", "display");
  const persistedKoreanMode = await page.locator('#displaySettingsForm [name="number_format"]').inputValue();
  const persistedKoreanPreview = await page.locator("#displayNumberPreview").innerText();
  const koreanAnalysis = await runPlayerAnalysis(page);

  await openWorkspaceSection(page, "settings", "display");
  await page.locator('#displaySettingsForm [name="number_format"]').selectOption(originalNumberMode);
  await Promise.all([
    page.waitForNavigation({ waitUntil: "networkidle" }),
    page.locator('#displaySettingsForm button[type="submit"]').click(),
  ]);
  await openWorkspaceSection(page, "settings", "display");
  const restoredOriginalMode = await page.locator('#displaySettingsForm [name="number_format"]').inputValue();

  await openWorkspaceSection(page, "players", "weapons");
  const weaponSelection = await selectPlayerForForm(page, "#weaponForm");
  await page.waitForFunction(() => (
    [...document.querySelectorAll('#weaponForm select[name="weapon"] option')]
      .some((option) => option.value)
  ));
  const weaponSelect = weaponSelection.form.locator('select[name="weapon"]');
  const weaponCode = await weaponSelect.locator("option").evaluateAll((options) => (
    options.find((option) => option.value)?.value || ""
  ));
  await weaponSelect.selectOption(weaponCode);
  await weaponSelection.form.locator('button[type="submit"]').click();
  await page.locator("#weaponBody .result-shell").waitFor({ timeout: 30000 });
  const weaponDetailTabs = await page.locator("#weaponBody [data-weapon-detail-view]").count();
  await page.locator('[data-weapon-detail-view="attachments"]').click();
  const attachmentPanel = page.locator('[data-weapon-detail-panel="attachments"]');
  const attachmentPanelVisible = await attachmentPanel.isVisible();
  const attachmentText = await attachmentPanel.innerText();
  const attachmentBars = await attachmentPanel.locator(".comparison-bar-row").count();
  const attachmentGroups = await attachmentPanel.locator("[data-attachment-group]").count();
  const attachmentGroupLabels = await attachmentPanel.locator("[data-attachment-group] summary").allInnerTexts();
  const attachmentHasConfidenceInterval = attachmentText.includes("95% 신뢰구간");
  const attachmentMinimumInput = attachmentPanel.locator("[data-weapon-attachment-min-matches]");
  await attachmentMinimumInput.fill("999999");
  await attachmentMinimumInput.press("Tab");
  await page.waitForTimeout(100);
  const attachmentHighMinimumEmpty = (await attachmentPanel.innerText()).includes(
    "최소 경기 수를 충족한 개별 파츠가 없습니다.",
  );
  await attachmentPanel.locator('[data-reset-weapon-attachment-filter="individual"]').click();
  const attachmentMinimumReset = await attachmentPanel.locator(
    "[data-weapon-attachment-min-matches]",
  ).inputValue();
  const attachmentGroupsAfterReset = await attachmentPanel.locator("[data-attachment-group]").count();
  const weaponAttachmentScreenshot = path.join(
    outputDir,
    "weapon-individual-attachment-analysis-desktop.png",
  );
  await page.locator("#weapon-lookup").scrollIntoViewIfNeeded();
  await page.screenshot({ path: weaponAttachmentScreenshot, fullPage: false });
  await page.locator('[data-weapon-detail-view="combinations"]').click();
  const combinationPanel = page.locator('[data-weapon-detail-panel="combinations"]');
  const combinationPanelVisible = await combinationPanel.isVisible();
  const combinationText = await combinationPanel.innerText();
  const combinationMinimumInput = combinationPanel.locator("[data-weapon-combination-min-matches]");
  await combinationMinimumInput.fill("999999");
  await combinationMinimumInput.press("Tab");
  await page.waitForTimeout(100);
  const combinationHighMinimumEmpty = (await combinationPanel.innerText()).includes(
    "최소 경기 수를 충족한 파츠 조합이 없습니다.",
  );
  await combinationPanel.locator('[data-reset-weapon-attachment-filter="combination"]').click();
  const combinationMinimumReset = await combinationPanel.locator(
    "[data-weapon-combination-min-matches]",
  ).inputValue();
  const combinationGroupsAfterReset = await combinationPanel.locator(
    "[data-attachment-combination-size]",
  ).count();
  const weaponScreenshot = path.join(outputDir, "weapon-attachment-analysis-desktop.png");
  await page.locator("#weapon-lookup").scrollIntoViewIfNeeded();
  await page.screenshot({ path: weaponScreenshot, fullPage: false });

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

  await openWorkspaceSection(page, "replay", "flight-paths");
  const flightDetails = page.locator("#flightPathForm details");
  if ((await flightDetails.getAttribute("open")) === null) {
    await flightDetails.locator("summary").click();
  }
  await page.locator('#flightPathForm input[name="top_per_map"]').fill("5");
  await page.locator('#flightPathForm input[name="recent_limit"]').fill("5");
  await page.locator('#flightPathForm input[name="route_limit"]').fill("1000");
  await page.locator('#flightPathForm button[type="submit"]').click();
  await page.locator("#flightPathStatus").filter({ hasText: "분석 완료" }).waitFor({ timeout: 60000 });
  await page.waitForFunction(() => {
    const image = document.querySelector("#flightPathMapImage");
    return Boolean(image?.complete && image.naturalWidth > 0 && image.naturalHeight > 0);
  });
  const flightPathStatus = await page.locator("#flightPathStatus").innerText();
  const flightMapOptions = await page.locator("#flightPathMapSelect option").count();
  const flightMapLines = await page.locator("#flightPathOverlay [data-flight-line]").count();
  const flightRankRows = await page.locator("#flightPathList [data-flight-row]").count();
  const flightOverflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  const flightScreenshot = path.join(outputDir, "flight-paths-desktop.png");
  await page.locator("#flightPathResult").scrollIntoViewIfNeeded();
  await page.screenshot({ path: flightScreenshot, fullPage: false });

  await openWorkspaceSection(page, "discord", "bot");
  await page.locator("#discordBotStatus").filter({ hasNotText: "확인 중" }).waitFor({ timeout: 30000 });
  const discordBotStatus = await page.locator("#discordBotStatus").innerText();
  const secretInputsEmpty = await page.locator(
    '#pubgApiKeyForm input[name="value"], #discordTokenForm input[name="value"]',
  ).evaluateAll((inputs) => inputs.every((input) => input.value === ""));
  const discordGuildOptions = await page.locator("#discordBotGuildSelect option").count();
  const discordOverflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  const discordScreenshot = path.join(outputDir, "discord-bot-desktop.png");
  await page.locator("#discord-bot-manager").screenshot({ path: discordScreenshot });

  const iconLoaded = await page.locator(".brand-mark").evaluate(
    (image) => image.complete && image.naturalWidth > 0 && image.naturalHeight > 0,
  );

  const items = matchApi.items || [];
  return {
    numberFormat: {
      originalNumberMode,
      koreanPreview,
      persistedKoreanPreview,
      persistedKoreanMode,
      restoredOriginalMode,
      actualAnalysisHasKoreanUnit: koreanAnalysis.hasKoreanLargeUnit,
      actualAnalysisUnconvertedLargeNumbers: koreanAnalysis.unconvertedLargeNumbers,
      koreanUnitsRendered: koreanPreview.includes("5만") && koreanPreview.includes("9,452")
        && persistedKoreanPreview.includes("5만") && persistedKoreanMode === "korean_units"
        && restoredOriginalMode === originalNumberMode
        && koreanAnalysis.hasKoreanLargeUnit
        && koreanAnalysis.unconvertedLargeNumbers.length === 0,
    },
    weaponAttachments: {
      weaponCode,
      tabCount: weaponDetailTabs,
      attachmentPanelVisible,
      combinationPanelVisible,
      attachmentBars,
      attachmentGroups,
      attachmentGroupsAfterReset,
      attachmentGroupLabels,
      attachmentHasConfidenceInterval,
      attachmentHighMinimumEmpty,
      attachmentMinimumReset,
      combinationHighMinimumEmpty,
      combinationMinimumReset,
      combinationGroupsAfterReset,
      hasNoAttachmentBasis: attachmentText.includes("노 파츠") && attachmentText.includes("교전 승률"),
      hasCombinationBasis: combinationText.includes("2개 이상의 파츠") && combinationText.includes("교전 승률"),
      hasWeaponScopedBasis: attachmentText.includes("그 무기에 장착돼 있던 파츠만")
        && attachmentText.includes("다른 무기의 파츠는 섞지")
        && combinationText.includes("다른 무기에 장착된 파츠는 포함하지"),
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
    flightPaths: {
      status: flightPathStatus,
      mapOptions: flightMapOptions,
      mapLines: flightMapLines,
      rankRows: flightRankRows,
      overflow: flightOverflow,
    },
    discordBot: {
      status: discordBotStatus,
      navigationLabel: await page.locator('[data-view-target="discord"]').innerText(),
      managerVisible: await page.locator("#discord-bot-manager").isVisible(),
      secretInputsEmpty,
      guildOptions: discordGuildOptions,
      overflow: discordOverflow,
    },
    iconLoaded,
    screenshots: {
      matchScreenshot,
      weaponScreenshot,
      weaponAttachmentScreenshot,
      landingScreenshot,
      comparisonScreenshot,
      flightScreenshot,
      discordScreenshot,
    },
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
    const workspaceContent = await runWorkspaceContentCheck(desktopPage);
    const player = await runPlayerAnalysis(desktopPage);
    const recommendations = await runRecommendationChecks(desktopPage);
    const playerScreenshot = path.join(outputDir, "player-intelligence-desktop.png");
    await desktopPage.screenshot({ path: playerScreenshot, fullPage: false });
    const features = await runExpandedFeatureChecks(desktopPage);
    const registryDimensions = await runRegistryAndDimensionChecks(desktopPage);
    const wholeMatchReplay = await runWholeMatchReplay(desktopPage);
    const audit = await runDataQualityAudit(desktopPage);
    const workspaceNavigation = await runWorkspaceNavigationCheck(desktopPage);
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
    const mobileRegistry = await runMobileRegistryCheck(mobilePage);
    const mobileLayout = await layoutDiagnostics(mobilePage);
    await mobileContext.close();

    const result = {
      baseUrl,
      desktop: {
        status: desktopResponse?.status(),
        player,
        recommendations,
        workspaceContent,
        features,
        registryDimensions,
        wholeMatchReplay,
        audit,
        workspaceNavigation,
        layout: desktopLayout,
        ...desktopMonitor,
      },
      mobile: {
        status: mobileResponse?.status(),
        player: mobilePlayer,
        registry: mobileRegistry,
        layout: mobileLayout,
        ...mobileMonitor,
      },
      screenshots: {
        playerScreenshot,
        recommendationScreenshot: recommendations.screenshot,
        auditScreenshot,
        mobileScreenshot,
        ...features.screenshots,
        ...registryDimensions.screenshots,
        wholeMatchReplay: wholeMatchReplay.screenshot,
        mobileRegistry: mobileRegistry.screenshot,
      },
    };
    const failed = [
      result.desktop.status !== 200,
      result.mobile.status !== 200,
      !result.desktop.player.hasPlayer,
      !result.desktop.player.tacticsReady,
      result.desktop.player.tacticsBackfillWaiting,
      result.desktop.player.tacticsSections < 6,
      !result.desktop.recommendations.hasDetailedFilters,
      !result.desktop.recommendations.hasLoadoutRecommendation,
      !result.desktop.recommendations.appliedMapCondition,
      !result.desktop.recommendations.hasAdjustedRates,
      result.desktop.recommendations.chartRows < 1,
      !result.desktop.recommendations.chartMetricApplied,
      !result.desktop.workspaceContent.valid,
      !result.mobile.player.hasPlayer,
      !result.mobile.player.tacticsReady,
      result.mobile.player.tacticsBackfillWaiting,
      !result.desktop.features.numberFormat.koreanUnitsRendered,
      result.desktop.features.weaponAttachments.tabCount !== 3,
      !result.desktop.features.weaponAttachments.attachmentPanelVisible,
      !result.desktop.features.weaponAttachments.combinationPanelVisible,
      !result.desktop.features.weaponAttachments.hasNoAttachmentBasis,
      !result.desktop.features.weaponAttachments.hasCombinationBasis,
      !result.desktop.features.weaponAttachments.hasWeaponScopedBasis,
      result.desktop.features.weaponAttachments.attachmentGroups < 1,
      result.desktop.features.weaponAttachments.attachmentGroupsAfterReset < 1,
      !result.desktop.features.weaponAttachments.attachmentGroupLabels.some((label) => label.includes("손잡이")),
      !result.desktop.features.weaponAttachments.attachmentHasConfidenceInterval,
      !result.desktop.features.weaponAttachments.attachmentHighMinimumEmpty,
      result.desktop.features.weaponAttachments.attachmentMinimumReset !== "1",
      !result.desktop.features.weaponAttachments.combinationHighMinimumEmpty,
      result.desktop.features.weaponAttachments.combinationMinimumReset !== "1",
      result.desktop.features.weaponAttachments.combinationGroupsAfterReset < 1,
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
      !result.desktop.features.iconLoaded,
      result.desktop.features.flightPaths.mapOptions < 1,
      result.desktop.features.flightPaths.mapLines < 1,
      result.desktop.features.flightPaths.rankRows < 1,
      result.desktop.features.flightPaths.overflow,
      !result.desktop.features.discordBot.secretInputsEmpty,
      !result.desktop.features.discordBot.status,
      !result.desktop.features.discordBot.navigationLabel.includes("Discord 봇"),
      !result.desktop.features.discordBot.managerVisible,
      result.desktop.features.discordBot.overflow,
      result.desktop.registryDimensions.registryRows < 1,
      result.desktop.registryDimensions.managementButtons < result.desktop.registryDimensions.registryRows * 2,
      result.desktop.registryDimensions.discordEditors < result.desktop.registryDimensions.registryRows,
      result.desktop.registryDimensions.discordChips < 1,
      result.desktop.registryDimensions.rowsByDimension.weapon < 1,
      result.desktop.registryDimensions.rowsByDimension.map < 1,
      result.desktop.registryDimensions.rowsByDimension.hour < 1,
      result.desktop.registryDimensions.weaponChartRows < 1,
      !result.desktop.registryDimensions.hasDetailedMetrics,
      result.desktop.wholeMatchReplay.participantCards <= 4,
      result.desktop.wholeMatchReplay.actorCheckboxes !== result.desktop.wholeMatchReplay.participantCards,
      result.desktop.wholeMatchReplay.defaultActorChecked !== 1,
      result.desktop.wholeMatchReplay.allActorChecked !== result.desktop.wholeMatchReplay.actorCheckboxes,
      result.desktop.wholeMatchReplay.allSelectionRenderMs > 5000,
      result.desktop.wholeMatchReplay.allSelectionEventWindow.rendered > 240,
      result.desktop.wholeMatchReplay.allSelectionEventWindow.total < result.desktop.wholeMatchReplay.allSelectionEventWindow.rendered,
      result.desktop.wholeMatchReplay.searchedActorCheckboxes < 1,
      result.desktop.wholeMatchReplay.searchedActorCheckboxes >= result.desktop.wholeMatchReplay.actorCheckboxes,
      result.desktop.wholeMatchReplay.restoredActorCheckboxes !== result.desktop.wholeMatchReplay.actorCheckboxes,
      result.desktop.wholeMatchReplay.restoredActorChecked !== result.desktop.wholeMatchReplay.actorCheckboxes,
      JSON.stringify(result.desktop.wholeMatchReplay.actorPriorityFixture.map((item) => item.group)) !== JSON.stringify([
        "focus", "focus_threat", "focus_threat", "focus_threat", "ally", "teammate_threat", "human", "bot",
      ]),
      JSON.stringify(result.desktop.wholeMatchReplay.actorPriorityFixture.map((item) => item.priority)) !== JSON.stringify([1, 2, 2, 2, 3, 4, 5, 6]),
      JSON.stringify(result.desktop.wholeMatchReplay.actorPriorityFixture.map((item) => item.label)) !== JSON.stringify([
        "1순위 · 이벤트 대상",
        "2순위 · 나를 기절·죽인 적군 (기절)",
        "2순위 · 나를 기절·죽인 적군 (죽임)",
        "2순위 · 나를 기절·죽인 적군 (기절·죽임)",
        "3순위 · 팀원",
        "4순위 · 팀원을 기절·죽인 적군 (죽임)",
        "5순위 · 그 외 사람",
        "6순위 · 봇",
      ]),
      result.desktop.wholeMatchReplay.eventTypeCheckboxes !== 9,
      result.desktop.wholeMatchReplay.defaultEventTypeChecked !== 9,
      result.desktop.wholeMatchReplay.typeNoneCount !== 0,
      result.desktop.wholeMatchReplay.multiTypeEventCount < 1,
      result.desktop.wholeMatchReplay.filteredEventTypes.some((eventType) => !["dbno", "kill"].includes(eventType)),
      !result.desktop.wholeMatchReplay.hasEnemy,
      !result.desktop.wholeMatchReplay.hasFocus,
      result.desktop.wholeMatchReplay.checkedLayers !== 8,
      result.desktop.wholeMatchReplay.eventCount < 1,
      !result.desktop.wholeMatchReplay.hasCombatEvent,
      result.desktop.wholeMatchReplay.canvas.opaque < 10,
      result.desktop.wholeMatchReplay.canvas.sampledColors < 4,
      !result.desktop.wholeMatchReplay.playbackAdvanced,
      result.mobile.registry.rows < 1,
      result.mobile.registry.editors < result.mobile.registry.rows,
      result.desktop.audit.failedRows > 0,
      !result.desktop.audit.hasScopeAndFreshness,
      !result.desktop.workspaceNavigation.stable,
      result.desktop.consoleErrors.length > 0,
      result.mobile.consoleErrors.length > 0,
      result.desktop.requestFailures.length > 0,
      result.mobile.requestFailures.length > 0,
      result.desktop.httpErrors.length > 0,
      result.mobile.httpErrors.length > 0,
      result.desktop.layout.blank || result.mobile.layout.blank,
      result.desktop.layout.overlay || result.mobile.layout.overlay,
      result.desktop.layout.documentWidth > result.desktop.layout.viewportWidth + 1,
      result.mobile.layout.documentWidth > result.mobile.layout.viewportWidth + 1,
      result.desktop.layout.overflowingButtons.length > 0,
      result.mobile.layout.overflowingButtons.length > 0,
    ].some(Boolean);
    const compactResult = {
      passed: !failed,
      baseUrl,
      status: {
        desktop: result.desktop.status,
        mobile: result.mobile.status,
      },
      playerAnalysis: {
        desktopHasPlayer: result.desktop.player.hasPlayer,
        desktopTacticsReady: result.desktop.player.tacticsReady,
        desktopTacticsSections: result.desktop.player.tacticsSections,
        desktopBackfillWaiting: result.desktop.player.tacticsBackfillWaiting,
        mobileHasPlayer: result.mobile.player.hasPlayer,
        mobileTacticsReady: result.mobile.player.tacticsReady,
        mobileBackfillWaiting: result.mobile.player.tacticsBackfillWaiting,
      },
      recommendations: result.desktop.recommendations,
      numberFormat: result.desktop.features.numberFormat,
      weaponAttachments: result.desktop.features.weaponAttachments,
      matchAnalysis: result.desktop.features.match,
      landingAnalysis: result.desktop.features.landing,
      ranking: result.desktop.features.ranking,
      comparison: result.desktop.features.comparison,
      flightPaths: result.desktop.features.flightPaths,
      discordBot: result.desktop.features.discordBot,
      registryDimensions: result.desktop.registryDimensions,
      wholeMatchReplay: result.desktop.wholeMatchReplay,
      audit: result.desktop.audit,
      workspaceContent: {
        checked: result.desktop.workspaceContent.checked,
        valid: result.desktop.workspaceContent.valid,
        failures: result.desktop.workspaceContent.failures.length,
      },
      workspaceNavigation: result.desktop.workspaceNavigation,
      errors: {
        desktopConsole: result.desktop.consoleErrors.length,
        mobileConsole: result.mobile.consoleErrors.length,
        desktopRequests: result.desktop.requestFailures.length,
        mobileRequests: result.mobile.requestFailures.length,
        desktopHttp: result.desktop.httpErrors.length,
        mobileHttp: result.mobile.httpErrors.length,
      },
      layout: {
        desktop: result.desktop.layout,
        mobile: result.mobile.layout,
      },
    };
    console.log(JSON.stringify(process.argv.includes("--compact") ? compactResult : result, null, 2));
    process.exitCode = failed ? 1 : 0;
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
