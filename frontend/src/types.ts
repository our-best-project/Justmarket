export type EventStatus =
  | "official_confirmed"
  | "developing"
  | "market_reacting"
  | "preliminary";

export type VerifyState = "verified" | "preliminary" | "observing";

export type ExpectedDirection = "利多" | "利空" | "中性";

export type SourceType = "official" | "media" | "gov";

export interface RelatedTicker {
  readonly ticker: string;
  readonly name: string;
}

export interface EventImage {
  readonly url: string;
  readonly alt: string;
  readonly credit: string;
  readonly source_url: string;
}

export interface NewsSource {
  readonly source: string;
  readonly source_type: SourceType;
  readonly url: string;
  readonly title: string;
  readonly published_at: string;
  readonly has_unique_detail: boolean;
}

export interface EventTimelineItem {
  readonly date: string;
  readonly title: string;
  readonly current?: boolean;
}

export interface MarketReactionPoint {
  readonly date: string;
  readonly close: number;
  readonly volume_ratio: number;
  readonly foreign_net: number;
  readonly trust_net: number;
  readonly dealer_net: number;
}

export interface MarketReaction {
  readonly ticker: string;
  readonly event_index: number;
  readonly event_date: string;
  readonly as_of: string;
  readonly return_1d: number | null;
  readonly return_3d: number | null;
  readonly return_5d: number | null;
  readonly volume_ratio: number | null;
  readonly foreign_consecutive_days: number | null;
  readonly series: readonly MarketReactionPoint[];
}

export interface MarketEvent {
  readonly event_id: string;
  readonly title: string;
  readonly deck: string;
  readonly summary: string;
  readonly date: string;
  readonly occurred_at_iso: string;
  readonly occurred_at_text: string;
  readonly status: EventStatus;
  readonly categories: readonly string[];
  readonly expected_direction?: ExpectedDirection | null;
  readonly stars: number;
  readonly market_validation: number | null;
  readonly verify_state: VerifyState;
  readonly source_count: number;
  /** LLM 標的產業別（bootstrap 0811 起提供；舊 fixture 沒有故選填） */
  readonly industries?: readonly string[];
  readonly related_tickers: readonly RelatedTicker[];
  readonly image: EventImage | null;
  readonly importance_reasons: readonly string[];
  readonly sources: readonly NewsSource[];
  readonly market_reaction: MarketReaction | null;
  readonly timeline: readonly EventTimelineItem[];
}

export interface EventEditionMeta {
  readonly edition_date: string;
  readonly as_of: string;
  readonly total_events: number;
  readonly total_sources: number;
  /** 有已評分事件的日期（新→舊，完整事件流日期選單用）。舊資料源沒有故選填 */
  readonly available_dates?: readonly string[];
}

export interface EventCatalog {
  readonly meta: EventEditionMeta;
  readonly categories: readonly string[];
  readonly events: readonly MarketEvent[];
}

export type DataStatus = "mock" | "ready" | "stale" | "unavailable";

export interface DashboardSeriesPoint {
  readonly date: string;
  readonly value: number;
}

export interface GlobalMarket {
  readonly id: string;
  readonly index: string;
  readonly name: string;
  readonly country: string;
  readonly currency: string;
  readonly timezone: string;
  readonly session: "closed" | "open" | "preopen";
  readonly tradeDate: string;
  readonly asOf: string;
  readonly delay: string;
  readonly change1d: number;
  readonly return5d: number;
  readonly return20d: number;
  readonly mapX: number;
  readonly mapY: number;
  readonly series20: readonly DashboardSeriesPoint[];
  readonly source: string;
  readonly status: DataStatus;
}

export interface TaiwanBreadth {
  readonly indexClose: number;
  readonly indexChange1d: number;
  readonly advancers: number;
  readonly unchanged: number;
  readonly decliners: number;
  readonly limitUp: number;
  readonly limitDown: number;
  readonly turnoverE: number;
  readonly turnoverVs20d: number;
  readonly medianReturn: number;
  readonly source: string;
  readonly asOf: string;
}

export interface IndustryBreadth {
  readonly id: string;
  readonly name: string;
  readonly advancers: number;
  readonly unchanged: number;
  readonly decliners: number;
  readonly return1d: number;
  readonly turnoverE: number;
}


/** 成交熱度泡泡：泡泡面積正比於成交值，顏色為當日漲跌。全部來自 chip_data。 */
export interface TurnoverBubble {
  readonly ticker: string;
  readonly name: string;
  readonly industry: string;
  readonly close: number;
  /** 成交值（億元） */
  readonly turnoverE: number;
  readonly change1d: number;
}



export interface RetailTopic {
  readonly id: string;
  readonly label: string;
  readonly stance: number;
  readonly volumeZ: number;
  readonly mentions: number;
}


export interface DashboardSourceStatus {
  readonly label: string;
  readonly status: DataStatus;
  readonly asOf: string;
}

export interface DashboardSnapshot {
  readonly generatedAt: string;
  readonly marketDate: string;
  readonly status: DataStatus;
  readonly insight: string;
  readonly dataCoverage: number;
  readonly globalMarkets: readonly GlobalMarket[];
  readonly taiwanBreadth: TaiwanBreadth;
  readonly industries: readonly IndustryBreadth[];
  readonly topTurnover: readonly TurnoverBubble[];
  readonly sourceStatuses: readonly DashboardSourceStatus[];
}

export type Dispose = () => void;
