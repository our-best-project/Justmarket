/**
 * 測試夾具——**不是**產品用的 mock。
 *
 * 正式程式碼已完全移除 mock 退路：取不到真資料就報錯，不再拿假數字充數。
 * 這份留在 tests/ 下，只給單元測試當確定性輸入用；放這裡才不會被 import 回 src/。
 */
import type {
  DashboardSeriesPoint,
  DashboardSnapshot,
  GlobalMarket,
} from "../../src/types";

const dates = [
  "06/19", "06/20", "06/23", "06/24", "06/25",
  "06/26", "06/27", "06/30", "07/01", "07/02",
  "07/03", "07/04", "07/07", "07/08", "07/09",
  "07/10", "07/11", "07/14", "07/15", "07/16",
] as const;

function series(
  start: number,
  steps: readonly number[],
): readonly DashboardSeriesPoint[] {
  let value = start;
  return dates.map((date, index) => {
    value *= 1 + (steps[index % steps.length] ?? 0) / 100;
    return { date, value: Number(value.toFixed(2)) };
  });
}

function market(
  input: Omit<GlobalMarket, "series20" | "source" | "status"> & {
    readonly pattern: readonly number[];
  },
): GlobalMarket {
  const { pattern, ...marketInput } = input;
  return {
    ...marketInput,
    series20: series(100, pattern),
    source: "Phase 0 模擬行情",
    status: "ready",
  };
}

export const dashboardFixture: DashboardSnapshot = {
  generatedAt: "2026-07-16T16:20:00+08:00",
  marketDate: "2026-07-16",
  status: "mock",
  insight:
    "權值股撐住指數，但上漲家數尚未過半；AI 供應鏈同時出現消息升溫與量價驗證。",
  dataCoverage: 0.96,
  globalMarkets: [
    market({
      id: "tw", index: "TAIEX", name: "臺灣加權", country: "台灣",
      currency: "TWD", timezone: "Asia/Taipei", session: "closed",
      tradeDate: "2026-07-16", asOf: "16:20", delay: "EOD",
      change1d: 0.72, return5d: -1.14, return20d: 3.28,
      mapX: 79, mapY: 54, pattern: [0.3, -0.4, 0.8, 0.2, -0.7, 1.1],
    }),
    market({
      id: "us-nasdaq", index: "NDX", name: "NASDAQ 100", country: "美國",
      currency: "USD", timezone: "America/New_York", session: "preopen",
      tradeDate: "2026-07-15", asOf: "04:00", delay: "前收",
      change1d: 1.04, return5d: 2.31, return20d: 5.42,
      mapX: 19, mapY: 43, pattern: [0.7, 0.4, -0.2, 1.0, -0.5, 0.8],
    }),
    market({
      id: "us-spx", index: "SPX", name: "S&P 500", country: "美國",
      currency: "USD", timezone: "America/New_York", session: "preopen",
      tradeDate: "2026-07-15", asOf: "04:00", delay: "前收",
      change1d: 0.63, return5d: 1.42, return20d: 3.16,
      mapX: 25, mapY: 49, pattern: [0.4, 0.2, -0.3, 0.6, -0.2, 0.5],
    }),
    market({
      id: "jp", index: "N225", name: "日經 225", country: "日本",
      currency: "JPY", timezone: "Asia/Tokyo", session: "closed",
      tradeDate: "2026-07-16", asOf: "14:30", delay: "EOD",
      change1d: -0.38, return5d: 0.91, return20d: 2.08,
      mapX: 88, mapY: 42, pattern: [0.6, -0.7, 0.4, 0.9, -0.6, 0.2],
    }),
    market({
      id: "kr", index: "KOSPI", name: "韓國綜合", country: "韓國",
      currency: "KRW", timezone: "Asia/Seoul", session: "closed",
      tradeDate: "2026-07-16", asOf: "14:30", delay: "EOD",
      change1d: 0.29, return5d: -0.44, return20d: 1.63,
      mapX: 81, mapY: 39, pattern: [-0.2, 0.5, -0.4, 0.7, 0.1, -0.3],
    }),
    market({
      id: "hk", index: "HSI", name: "香港恆生", country: "香港",
      currency: "HKD", timezone: "Asia/Hong_Kong", session: "closed",
      tradeDate: "2026-07-16", asOf: "16:08", delay: "EOD",
      change1d: -1.12, return5d: -2.08, return20d: -4.11,
      mapX: 74, mapY: 53, pattern: [-0.5, 0.2, -0.8, -0.4, 0.3, -0.7],
    }),
    market({
      id: "cn", index: "CSI300", name: "滬深 300", country: "中國",
      currency: "CNY", timezone: "Asia/Shanghai", session: "closed",
      tradeDate: "2026-07-16", asOf: "15:00", delay: "EOD",
      change1d: -0.54, return5d: 0.22, return20d: -1.72,
      mapX: 70, mapY: 45, pattern: [0.1, -0.4, 0.5, -0.6, 0.2, -0.3],
    }),
    market({
      id: "eu", index: "SX5E", name: "EURO STOXX 50", country: "歐元區",
      currency: "EUR", timezone: "Europe/Paris", session: "preopen",
      tradeDate: "2026-07-15", asOf: "23:30", delay: "前收",
      change1d: 0.18, return5d: -0.61, return20d: 0.84,
      mapX: 50, mapY: 35, pattern: [0.2, -0.3, 0.1, 0.4, -0.2, 0.1],
    }),
  ],
  taiwanBreadth: {
    indexClose: 23618.17,
    indexChange1d: 0.72,
    advancers: 612,
    unchanged: 91,
    decliners: 326,
    limitUp: 24,
    limitDown: 3,
    turnoverE: 4286.0,
    turnoverVs20d: 1.18,
    medianReturn: 0.21,
    source: "TWSE / TPEx Phase 0 模擬彙整",
    asOf: "2026-07-16 16:20",
  },
  industries: [
    { id: "semi", name: "半導體", advancers: 81, unchanged: 9, decliners: 42, return1d: 1.42, turnoverE: 1462.0 },
    { id: "computer", name: "電腦及週邊", advancers: 53, unchanged: 7, decliners: 31, return1d: 0.88, turnoverE: 674.0 },
    { id: "electronic", name: "電子零組件", advancers: 62, unchanged: 11, decliners: 49, return1d: 0.31, turnoverE: 518.0 },
    { id: "shipping", name: "航運", advancers: 19, unchanged: 2, decliners: 9, return1d: 1.76, turnoverE: 321.0 },
    { id: "finance", name: "金融保險", advancers: 22, unchanged: 6, decliners: 18, return1d: 0.18, turnoverE: 287.0 },
    { id: "electric", name: "電機機械", advancers: 38, unchanged: 8, decliners: 34, return1d: -0.14, turnoverE: 213.0 },
    { id: "construction", name: "建材營造", advancers: 17, unchanged: 5, decliners: 24, return1d: -0.62, turnoverE: 139.0 },
    { id: "biotech", name: "生技醫療", advancers: 29, unchanged: 8, decliners: 41, return1d: -0.91, turnoverE: 124.0 },
    { id: "steel", name: "鋼鐵", advancers: 10, unchanged: 7, decliners: 25, return1d: -1.08, turnoverE: 98.0 },
    { id: "tourism", name: "觀光餐旅", advancers: 11, unchanged: 4, decliners: 20, return1d: -1.34, turnoverE: 61.0 },
  ],
  topTurnover: [
    { ticker: "2330", name: "台積電", industry: "半導體業", close: 1105, turnoverE: 604, change1d: -1.66 },
    { ticker: "2408", name: "南亞科", industry: "半導體業", close: 148, turnoverE: 490, change1d: 3.15 },
    { ticker: "2344", name: "華邦電", industry: "半導體業", close: 42.6, turnoverE: 318, change1d: 1.18 },
    { ticker: "2454", name: "聯發科", industry: "半導體業", close: 1390, turnoverE: 283, change1d: -2.0 },
    { ticker: "2317", name: "鴻海", industry: "其他電子業", close: 235, turnoverE: 217, change1d: 2.32 },
  ],
  // 「散戶語意」已隨散戶溫度計面板一併移除——沒有那個資料來源，狀態列不該還掛著它
  sourceStatuses: [
    { label: "台股行情", status: "mock", asOf: "16:20" },
    { label: "全球指數", status: "mock", asOf: "各市場前收" },
    { label: "新聞事件", status: "mock", asOf: "16:20" },
  ],
};
