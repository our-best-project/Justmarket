import { describeMarket } from "../components/market-narrative";
import type { DashboardSnapshot, GlobalMarket } from "../types";
import { API_BASE } from "./api-base";
import type { DashboardSource } from "./dashboard-source";

/**
 * 從後端取「今日大局」資料。
 *
 * 已接真資料的三塊：
 *   GLOBAL PULSE（各國大盤指數）  GET /market/global   ← Yahoo chart API，每日盤後 upsert
 *   台股盤面（漲跌家數等）        GET /market/breadth  ← chip_data 聚合
 *   產業表現                      同上                 ← chip_data × tickers.industry
 *
 * 成交熱度（前 12 大成交值個股）與多空領土戰（漲跌家數）同樣來自 /market/breadth。
 * 原本的主題板塊、消息關注度、散戶情緒三塊 mock 已移除——沒有資料來源就不畫，
 * 畫面上留假數字比留空更糟。
 *
 * API 位址見 ./api-base.ts：預設走相對路徑 /api/v1（由 vite dev proxy 轉到後端），
 * GitHub Pages 上改用 build 期注入的絕對網址。
 *
 * ⚠️ 沒有 mock 退路。任一端點失敗就整個拋錯，由 main.ts 顯示錯誤頁。
 * 早期版本會靜默退回 mock，實測後果是：畫面顯示三週前的假數字、四顆資料狀態燈
 * 全亮「真實」、頁尾還宣稱「無模擬內容」——使用者沒有任何辦法分辨。
 * 一個講市場資料的產品，寧可什麼都不顯示，也不能顯示分不出真假的數字。
 */
export class FetchDashboardSource implements DashboardSource {
  public constructor(private readonly apiBase: string = API_BASE) {}

  public async loadToday(): Promise<DashboardSnapshot> {
    // 兩塊都必須成功。缺一塊就是半真半假的畫面，那比整頁報錯更難察覺。
    const [globalMarkets, breadth] = await Promise.all([
      this.loadGlobal(),
      this.loadBreadth(),
    ]);
    const latestGlobal = globalMarkets.map((market) => market.tradeDate).sort().at(-1) ?? "";
    return {
      globalMarkets,
      ...breadth,
      dataCoverage: 1,
      marketDate: breadth.taiwanBreadth.asOf,
      // 導言由盤面推導，不寫死。寫死的文案會和同頁真數字打架
      // （實測那句「上漲家數尚未過半」，當天實際 54% 已過半）。
      insight: describeMarket(breadth.taiwanBreadth),
      status: "ready",
      sourceStatuses: [
        { label: "台股行情", status: "ready", asOf: `${breadth.taiwanBreadth.asOf} · ${breadth.taiwanBreadth.source}` },
        { label: "全球指數", status: "ready", asOf: `${latestGlobal} · Yahoo Finance` },
      ],
      generatedAt: new Date().toISOString(),
    };
  }

  private async loadGlobal(): Promise<GlobalMarket[]> {
    const res = await fetch(`${this.apiBase}/market/global`);
    if (!res.ok) {
      throw new Error(`全球指數取用失敗：GET /market/global 回 HTTP ${res.status}`);
    }
    const data = (await res.json()) as GlobalMarket[];
    if (!Array.isArray(data) || data.length === 0) {
      throw new Error("全球指數取用失敗：/market/global 回了空清單");
    }
    return data;
  }

  /** 台股盤面廣度＋產業表現（由 chip_data 聚合，非外部來源）。 */
  private async loadBreadth(): Promise<
    Pick<DashboardSnapshot, "taiwanBreadth" | "industries" | "topTurnover">
  > {
    const res = await fetch(`${this.apiBase}/market/breadth`);
    if (!res.ok) {
      throw new Error(`台股盤面取用失敗：GET /market/breadth 回 HTTP ${res.status}`);
    }
    const data = (await res.json()) as {
      breadth?: DashboardSnapshot["taiwanBreadth"];
      industries?: DashboardSnapshot["industries"];
      topTurnover?: DashboardSnapshot["topTurnover"];
    };
    if (!data.breadth || !data.industries?.length || !data.topTurnover?.length) {
      throw new Error("台股盤面取用失敗：/market/breadth 回應缺少 breadth／industries／topTurnover");
    }
    return {
      taiwanBreadth: data.breadth,
      industries: data.industries,
      topTurnover: data.topTurnover,
    };
  }
}
