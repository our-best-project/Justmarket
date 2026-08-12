import { afterEach, describe, expect, it, vi } from "vitest";

import { FetchDashboardSource, FetchEventSource } from "../../src/data";
import { eventsFixture } from "../fixtures/events.fixture";
import { dashboardFixture } from "../fixtures/dashboard.fixture";

afterEach(() => {
  vi.unstubAllGlobals();
});

const ok = (body: unknown) => ({ ok: true, status: 200, json: () => Promise.resolve(body) });
const fail = (status: number) => ({ ok: false, status, json: () => Promise.resolve({}) });

/**
 * 這組測試守的是一條產品規則：**取不到真資料就報錯，絕不拿假數字充數。**
 *
 * 專案早期兩支 source 都會靜默回退 mock。實測後果：畫面顯示三週前的假數字、
 * 四顆資料狀態燈全亮「真實」、頁尾還宣稱「無模擬內容」——使用者沒有任何辦法
 * 分辨。一個講市場資料的產品不能這樣。
 */
describe("EventSource：失敗必須拋錯，不得回退假資料", () => {
  it("後端不可用時拋錯", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new Error("offline"))));
    await expect(new FetchEventSource().load()).rejects.toThrow();
  });

  it("HTTP 非 2xx 時拋錯，錯誤訊息點名端點與狀態碼", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(fail(503))));
    await expect(new FetchEventSource().load()).rejects.toThrow(/bootstrap.*503/);
  });

  it("回應格式不符時拋錯，不當成空清單吞掉", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(ok({ meta: null }))));
    await expect(new FetchEventSource().load()).rejects.toThrow(/格式不符/);
  });

  it("正常時原樣回傳後端資料", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(ok(eventsFixture))));
    await expect(new FetchEventSource().load()).resolves.toEqual(eventsFixture);
  });

  it("搜尋失敗回空結果而非拋錯——這條路徑刻意不同：給空結果比中斷整頁好", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new Error("offline"))));
    await expect(new FetchEventSource().search("台積電")).resolves.toMatchObject({ count: 0 });
  });
});

describe("DashboardSource：兩支端點缺一即拋錯", () => {
  const stub = (globalOk: boolean, breadthOk: boolean) =>
    vi.stubGlobal("fetch", vi.fn((url: string) =>
      Promise.resolve(url.includes("/market/global")
        ? (globalOk ? ok(dashboardFixture.globalMarkets) : fail(500))
        : (breadthOk
          ? ok({
            breadth: dashboardFixture.taiwanBreadth,
            industries: dashboardFixture.industries,
            topTurnover: dashboardFixture.topTurnover,
          })
          : fail(500)))));

  it("全球指數掛掉 → 拋錯（不畫半真半假的頁面）", async () => {
    stub(false, true);
    await expect(new FetchDashboardSource().loadToday()).rejects.toThrow(/全球指數/);
  });

  it("台股盤面掛掉 → 拋錯", async () => {
    stub(true, false);
    await expect(new FetchDashboardSource().loadToday()).rejects.toThrow(/台股盤面/);
  });

  it("topTurnover 缺漏 → 拋錯，不再默默用假泡泡補位", async () => {
    vi.stubGlobal("fetch", vi.fn((url: string) =>
      Promise.resolve(url.includes("/market/global")
        ? ok(dashboardFixture.globalMarkets)
        : ok({ breadth: dashboardFixture.taiwanBreadth, industries: dashboardFixture.industries }))));
    await expect(new FetchDashboardSource().loadToday()).rejects.toThrow(/topTurnover/);
  });

  it("兩支都正常 → 覆蓋率 100%、日期與導言跟著真資料走", async () => {
    stub(true, true);
    const snap = await new FetchDashboardSource().loadToday();
    expect(snap.status).toBe("ready");
    expect(snap.dataCoverage).toBe(1);
    expect(snap.marketDate).toBe(dashboardFixture.taiwanBreadth.asOf);
    expect(snap.insight).toContain("上漲");
    expect(snap.sourceStatuses.every((s) => s.status === "ready")).toBe(true);
  });
});
