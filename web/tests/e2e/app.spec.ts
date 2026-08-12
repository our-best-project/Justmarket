import { expect, test } from "@playwright/test";

const FIRST_EVENT_ID = "evt_20260716_2330_001";
const FIRST_EVENT_TITLE = "台積電上修全年展望，AI 加速器需求延續至明年";

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    const originalAdd = window.addEventListener.bind(window);
    const originalRemove = window.removeEventListener.bind(window);
    const balance = { resize: 0 };

    window.addEventListener = ((type: string, listener: EventListenerOrEventListenerObject, options?: boolean | AddEventListenerOptions) => {
      if (type === "resize") balance.resize += 1;
      originalAdd(type, listener, options);
    }) as typeof window.addEventListener;
    window.removeEventListener = ((type: string, listener: EventListenerOrEventListenerObject, options?: boolean | EventListenerOptions) => {
      if (type === "resize") balance.resize -= 1;
      originalRemove(type, listener, options);
    }) as typeof window.removeEventListener;

    Object.defineProperty(window, "__eventSignalListenerBalance", {
      configurable: false,
      value: balance,
    });
  });
});

test("serves every hash route and falls back to home", async ({ page }) => {
  const routes = [
    ["#home", "把分散新聞，"],
    ["#board", "今日大局"],
    ["#all", "完整事件流"],
    ["#watchlist", "自選"],
    ["#method", "方法：從新聞到可驗證的事件"],
    [`#event-${FIRST_EVENT_ID}`, FIRST_EVENT_TITLE],
    ["#event-missing", "找不到這個事件"],
    ["#unknown", "把分散新聞，"],
  ] as const;

  for (const [hash, heading] of routes) {
    await page.goto(`./${hash}`);
    const target = hash === "#event-missing"
      ? page.locator(".empty b")
      : page.locator("h1");
    await expect(target).toContainText(heading);
  }
});

test("filters the event list with the date select", async ({ page }) => {
  await page.goto("./#all");

  const rows = page.locator("[data-stream] .row");
  await expect(rows).toHaveCount(10);

  // 選單換日會整頁重繪（不是隱藏列），所以斷言 count 而非 :visible
  await page.locator("[data-date-select]").selectOption("2026-07-15");
  await expect(page.locator("[data-stream] .row")).toHaveCount(2);

  await page.locator("[data-date-select]").selectOption("all");
  await expect(page.locator("[data-stream] .row")).toHaveCount(10);
});

test("persists watchlist changes across reloads", async ({ page }) => {
  await page.goto("./#all");
  const firstWatchButton = page.locator(`[data-mark="${FIRST_EVENT_ID}"]`);

  await firstWatchButton.click();
  await expect(firstWatchButton).toHaveAttribute("aria-pressed", "true");
  await page.reload();
  await expect(page.locator(`[data-mark="${FIRST_EVENT_ID}"]`)).toHaveAttribute(
    "aria-pressed",
    "true",
  );

  await page.goto("./#watchlist");
  await expect(page.getByText(FIRST_EVENT_TITLE, { exact: true })).toBeVisible();
  await page.locator(`[data-mark="${FIRST_EVENT_ID}"]`).click();
  await expect(page.getByText("自選是空的", { exact: true })).toBeVisible();
});

test("pauses the ambient 3D scene while the low-power board is open", async ({ page }) => {
  await page.goto("./#home");
  const listenerBalance = async () =>
    page.evaluate(() => {
      const state = window as typeof window & {
        __eventSignalListenerBalance: { resize: number };
      };
      return state.__eventSignalListenerBalance.resize;
    });

  const baseline = await listenerBalance();
  expect(baseline).toBe(1);

  for (let iteration = 0; iteration < 10; iteration += 1) {
    await page.goto("./#board");
    expect(await listenerBalance()).toBe(0);
    await page.goto("./#home");
    expect(await listenerBalance()).toBe(baseline);
  }
});

test("loads runtime assets without console or HTTP errors", async ({ page }) => {
  const errors: string[] = [];
  const sceneImages = new Set<string>();
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("response", (response) => {
    if (!response.ok()) errors.push(`${response.status()} ${response.url()}`);
    const pathname = new URL(response.url()).pathname;
    if (/\/(?:bg|fg)-[^/]+\.(?:webp|png)$/.test(pathname)) {
      sceneImages.add(pathname);
    }
  });

  await page.goto("./#home");
  await expect(page.locator("#person-canvas")).toBeVisible();
  await page.waitForTimeout(500);

  expect(errors).toEqual([]);
  expect([...sceneImages].filter((path) => path.endsWith(".webp"))).toHaveLength(
    2,
  );
  expect([...sceneImages].filter((path) => path.endsWith(".png"))).toEqual([]);
});

test("renders and links every Phase 0 dashboard section", async ({ page }) => {
  const errors: string[] = [];
  const boardAssets = new Set<string>();
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("response", (response) => {
    const pathname = new URL(response.url()).pathname;
    if (/\/board-bg-[^/]+\.webp$/.test(pathname)) {
      boardAssets.add(pathname);
    }
  });

  await page.goto("./#board");

  await expect(page.locator(".global-pulse")).toBeVisible();
  await expect(page.locator(".world-node")).toHaveCount(8);
  await expect(page.locator(".industry-row")).toHaveCount(10);
  await expect(page.locator(".theme-stock")).toHaveCount(12);
  await expect(page.locator(".attention-point")).toHaveCount(10);
  await expect(page.locator(".retail-point")).toHaveCount(6);
  await expect(page.locator(".selection-inspector")).toContainText("台積電");
  expect([...boardAssets]).toHaveLength(1);

  await page.locator('[data-market="hk"]').first().click();
  await expect(page.locator("[data-market-detail] h3")).toHaveText("香港恆生");
  await expect(page.locator('[data-market="hk"]').first()).toHaveAttribute("aria-pressed", "true");

  await page.locator('[data-stock="2603"]').first().click();
  await expect(page.locator(".selection-inspector")).toContainText("長榮");
  await expect(page.locator('[data-stock="2603"]').first()).toHaveAttribute("aria-pressed", "true");

  expect(errors).toEqual([]);
});

test("falls back to PNG when WebP is unavailable", async ({ page }) => {
  const sceneImages = new Set<string>();
  await page.addInitScript(() => {
    const originalToDataUrl = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function toDataURL(
      type?: string,
      quality?: number,
    ): string {
      if (type === "image/webp") return "data:image/png;base64,";
      return originalToDataUrl.call(this, type, quality);
    };
  });
  page.on("response", (response) => {
    const pathname = new URL(response.url()).pathname;
    if (/\/(?:bg|fg)-[^/]+\.(?:webp|png)$/.test(pathname)) {
      sceneImages.add(pathname);
    }
  });

  await page.goto("./#home");
  await page.waitForTimeout(500);

  expect([...sceneImages].filter((path) => path.endsWith(".png"))).toHaveLength(
    2,
  );
  expect([...sceneImages].filter((path) => path.endsWith(".webp"))).toEqual([]);
});
