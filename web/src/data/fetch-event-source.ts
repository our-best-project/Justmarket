import type { EventCatalog, MarketEvent } from "../types";
import { API_BASE } from "./api-base";
import type { EventSource, SearchResult } from "./event-source";

/**
 * ⚠️ 沒有 mock 退路。取不到就拋錯，由 main.ts 顯示錯誤頁。
 *
 * 早期版本失敗時會靜默回退 eventsMock，畫面上沒有任何提示——使用者會把
 * 三週前的假事件當成今天的新聞。search() 那條路徑一直是對的（回空結果不回
 * mock），現在把 load() 也拉齊。
 */
export class FetchEventSource implements EventSource {
  public constructor(private readonly apiBase: string = API_BASE) {}

  public async load(): Promise<EventCatalog> {
    const response = await fetch(`${this.apiBase}/demo/bootstrap`);
    if (!response.ok) {
      throw new Error(`事件清單取用失敗：GET /demo/bootstrap 回 HTTP ${response.status}`);
    }
    const catalog = (await response.json()) as EventCatalog;
    if (
      !catalog ||
      !catalog.meta ||
      !Array.isArray(catalog.categories) ||
      !Array.isArray(catalog.events)
    ) {
      throw new Error("事件清單取用失敗：/demo/bootstrap 回應格式不符");
    }
    return catalog;
  }

  public async search(query: string): Promise<SearchResult> {
    const q = query.trim();
    if (!q) {
      return { query: q, count: 0, categories: ["全部"], events: [] };
    }
    try {
      const response = await fetch(
        `${this.apiBase}/demo/search?q=${encodeURIComponent(q)}&limit=50`,
      );
      if (!response.ok) {
        throw new Error(`GET /demo/search -> HTTP ${response.status}`);
      }
      const result = (await response.json()) as SearchResult;
      if (!result || !Array.isArray(result.events)) {
        throw new Error("Invalid search response");
      }
      return result;
    } catch (error) {
      // 搜尋失敗不回退 mock——給假結果比給空結果更糟，使用者會以為真的只有這些
      console.warn("[FetchEventSource] 搜尋失敗：", error);
      return { query: q, count: 0, categories: ["全部"], events: [] };
    }
  }

  public async eventsByDay(date: string): Promise<readonly MarketEvent[]> {
    try {
      const response = await fetch(
        `${this.apiBase}/demo/day?date=${encodeURIComponent(date)}`,
      );
      if (!response.ok) {
        throw new Error(`GET /demo/day -> HTTP ${response.status}`);
      }
      const result = (await response.json()) as { events?: MarketEvent[] };
      return Array.isArray(result?.events) ? result.events : [];
    } catch (error) {
      // 與 search 同一原則：失敗回空、不回退假資料
      console.warn("[FetchEventSource] 取單日事件失敗：", error);
      return [];
    }
  }

  public async getEvent(eventId: string): Promise<MarketEvent | undefined> {
    try {
      const response = await fetch(
        `${this.apiBase}/demo/events/${encodeURIComponent(eventId)}`,
      );
      if (response.status === 404) {
        return undefined;      // 事件真的不存在，交給詳情頁顯示 404 畫面
      }
      if (!response.ok) {
        throw new Error(`GET /demo/events -> HTTP ${response.status}`);
      }
      return (await response.json()) as MarketEvent;
    } catch (error) {
      console.warn("[FetchEventSource] 取單一事件失敗：", error);
      return undefined;
    }
  }
}
