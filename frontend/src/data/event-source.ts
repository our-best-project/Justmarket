import type { EventCatalog, MarketEvent } from "../types";

export interface SearchResult {
  readonly query: string;
  readonly count: number;
  readonly categories: readonly string[];
  readonly events: readonly MarketEvent[];
}

export interface EventSource {
  load(): Promise<EventCatalog>;
  /** 關鍵字搜尋（公司名、股號或事件字詞）。後端不可用時回傳空結果，不讓畫面炸掉。 */
  search(query: string): Promise<SearchResult>;
  /** 依 id 取單一事件；找不到回 undefined。詳情頁在目錄裡找不到時用它補。 */
  getEvent(eventId: string): Promise<MarketEvent | undefined>;
  /** 指定收盤日的全部事件（完整事件流日期選單用）。選填——沒有實作就只能看已載入的日期。 */
  eventsByDay?(date: string): Promise<readonly MarketEvent[]>;
}
