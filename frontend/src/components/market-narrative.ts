import type { TaiwanBreadth } from "../types";

/**
 * 由當日盤面資料組出敘述文字。
 *
 * 為什麼要有這支：原本標題下的導言與台股盤面那句話都是寫死的文案，
 * 而且會和同一頁的真資料互相矛盾——2026-08-06 實測：
 *   導言寫「上漲家數尚未過半」，實際 672 / 562 已達 54%。
 *   盤面寫「指數由權值股撐住」，實際指數 −0.48% 而多數個股收紅，
 *   權值股是拖累不是撐住，講反了。
 * 假文案配真數字比純 mock 更糟：讀者無從分辨哪一句能信。
 *
 * ⚠️ 一律只做「描述已發生的事」。不寫任何預測、建議或後市看法——
 * 這是專案的法規底線（不預測、不報明牌）。
 */

const pct = (value: number): string => `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;

/** 收紅個股佔比（平盤不列入分母，與領土戰同一套算法）。 */
export function advanceShare(breadth: TaiwanBreadth): number {
  const contested = breadth.advancers + breadth.decliners;
  return contested > 0 ? breadth.advancers / contested : 0.5;
}

/**
 * 指數方向與家數方向是否背離。
 * 這是盤面最值得指出的事實：指數由市值加權，家數是等權，兩者不同調就代表
 * 漲跌集中在少數權值股身上。單看指數會誤判當天的實際賺賠面。
 */
export function isDiverging(breadth: TaiwanBreadth): boolean {
  const breadthUp = breadth.advancers > breadth.decliners;
  const indexUp = breadth.indexChange1d > 0;
  return breadthUp !== indexUp && breadth.indexChange1d !== 0;
}

/** 標題下的導言。 */
export function describeMarket(breadth: TaiwanBreadth): string {
  const share = Math.round(advanceShare(breadth) * 100);
  const parts = [
    `上漲 ${breadth.advancers} 家、下跌 ${breadth.decliners} 家，收紅個股佔 ${share}%`,
  ];
  if (isDiverging(breadth)) {
    parts.push(
      breadth.indexChange1d < 0
        ? `但指數收 ${pct(breadth.indexChange1d)}，與家數方向相反——跌的是權值股`
        : `但指數收 ${pct(breadth.indexChange1d)}，與家數方向相反——漲的是權值股`,
    );
  } else {
    parts.push(`指數同向收 ${pct(breadth.indexChange1d)}`);
  }
  const volume = breadth.turnoverVs20d;
  parts.push(
    volume >= 1.2 ? `成交值放大到 20 日均值的 ${volume.toFixed(2)} 倍`
      : volume <= 0.8 ? `成交值萎縮到 20 日均值的 ${volume.toFixed(2)} 倍`
      : `成交值為 20 日均值的 ${volume.toFixed(2)} 倍`,
  );
  return `${parts.join("；")}。`;
}

/** 台股盤面面板裡的那一句。指數與中位數的落差是這塊的重點。 */
export function describeBreadthPanel(breadth: TaiwanBreadth): string {
  const median = pct(breadth.medianReturn);
  if (isDiverging(breadth)) {
    return breadth.indexChange1d < 0
      ? `指數收 ${pct(breadth.indexChange1d)}，但個股中位數為 ${median}、上漲家數多於下跌——是權值股在拖，不是全面下跌。`
      : `指數收 ${pct(breadth.indexChange1d)}，但個股中位數為 ${median}、下跌家數多於上漲——由權值股撐住，不是全面普漲。`;
  }
  return `指數收 ${pct(breadth.indexChange1d)}，個股中位數 ${median}，方向一致。`;
}
