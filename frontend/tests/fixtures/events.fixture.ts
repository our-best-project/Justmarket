/**
 * 測試夾具——**不是**產品用的 mock。
 *
 * 正式程式碼已完全移除 mock 退路：取不到真資料就報錯，不再拿假數字充數。
 * 這份留在 tests/ 下，只給單元測試當確定性輸入用；放這裡才不會被 import 回 src/。
 */
import type {
  EventCatalog,
  NewsSource,
  MarketEvent,
  MarketReaction,
  SourceType,
} from "../../src/types";

interface Outlet {
  readonly source: string;
  readonly source_type: SourceType;
  readonly url: string;
}

const outlet = {
  mops: { source: "MOPS", source_type: "official", url: "https://mops.twse.com.tw/mops/web/index" },
  anue: { source: "鉅亨網", source_type: "media", url: "https://news.cnyes.com/" },
  udn: { source: "經濟日報", source_type: "media", url: "https://money.udn.com/" },
  ctee: { source: "工商時報", source_type: "media", url: "https://www.ctee.com.tw/" },
  twse: { source: "臺灣證券交易所", source_type: "official", url: "https://www.twse.com.tw/" },
  gov: { source: "主管機關公告", source_type: "gov", url: "https://www.gov.tw/" },
} satisfies Record<string, Outlet>;

function source(
  key: keyof typeof outlet,
  title: string,
  time: string,
  unique = false,
): NewsSource {
  return {
    ...outlet[key],
    title,
    published_at: time,
    has_unique_detail: unique,
  };
}

function market(
  ticker: string,
  values: readonly number[],
  returns: readonly (number | null)[],
  volumeRatio: number,
  foreignDays: number,
): MarketReaction {
  const dates = ["07/10", "07/11", "07/14", "07/15", "07/16", "07/17", "07/18"];
  const volumes = [0.82, 0.91, 1.04, volumeRatio, 1.26, 1.08, 0.96];
  const foreign = [-12_000_000, 8_000_000, 21_000_000, 48_000_000, 36_000_000, 18_000_000, 7_000_000];
  const trust = [-1_500_000, 1_200_000, 2_800_000, 6_400_000, 5_100_000, -900_000, 1_100_000];
  const dealer = [600_000, -400_000, 900_000, 2_100_000, -1_300_000, 700_000, 300_000];

  return {
    ticker,
    event_index: 3,
    event_date: "2026-07-15",
    as_of: "2026-07-18",
    return_1d: returns[0] ?? null,
    return_3d: returns[1] ?? null,
    return_5d: returns[2] ?? null,
    volume_ratio: volumeRatio,
    foreign_consecutive_days: foreignDays,
    series: dates.map((date, index) => ({
      date,
      close: values[index] ?? 0,
      volume_ratio: volumes[index] ?? 0,
      foreign_net: foreign[index] ?? 0,
      trust_net: trust[index] ?? 0,
      dealer_net: dealer[index] ?? 0,
    })),
  };
}

const events: readonly MarketEvent[] = [
  {
    event_id: "evt_20260716_2330_001",
    title: "台積電上修全年展望，AI 加速器需求延續至明年",
    deck: "法說釋出更明確的需求能見度，市場焦點從本季數字轉向先進製程產能。",
    summary: "台積電在法說會上調全年營收展望，主因 AI 加速器與高效能運算需求持續。公司同時表示先進製程產能仍偏緊，海外擴產將依客戶承諾逐步推進。",
    date: "2026-07-16", occurred_at_iso: "2026-07-16T14:10:00+08:00", occurred_at_text: "今天 14:10",
    status: "official_confirmed", categories: ["法說", "財報"], stars: 5, market_validation: 88, verify_state: "verified",
    source_count: 9, related_tickers: [{ ticker: "2330", name: "台積電" }],
    image: { url: "https://images.unsplash.com/photo-1518770660439-4636190af475?auto=format&fit=crop&w=1800&q=88", alt: "半導體晶片與電路板特寫", credit: "Unsplash・示意影像", source_url: "https://unsplash.com/s/photos/semiconductor" },
    importance_reasons: ["官方法說已確認", "影響大型權值股與半導體供應鏈", "多家媒體與法人同步關注"],
    sources: [
      source("mops", "台積電法人說明會簡報與重大訊息", "2026-07-16T14:00:00+08:00", true),
      source("udn", "台積電上修展望，AI 需求帶動先進製程", "2026-07-16T14:22:00+08:00"),
      source("ctee", "台積電法說三重點：需求、產能與海外布局", "2026-07-16T14:36:00+08:00", true),
      source("anue", "台積電法說報喜，全年營收展望再提高", "2026-07-16T14:41:00+08:00"),
    ],
    market_reaction: market("2330", [98.2, 99.1, 100, 101.8, 104.7, 106.8, 106.2], [0.018, 0.068, null], 1.8, 4),
    timeline: [
      { date: "07/08 16:30", title: "公告法說會日期與議程" },
      { date: "07/10 13:40", title: "6 月營收創同期新高" },
      { date: "07/16 14:10", title: "法說上修全年展望", current: true },
    ],
  },
  {
    event_id: "evt_20260716_policy_002",
    title: "美國擬擴大半導體設備限制，供應鏈評估新一輪影響",
    deck: "政策細節仍待正式公布，設備與成熟製程供應鏈先進入風險重估。",
    summary: "外媒披露美國可能調整半導體設備出口規範，範圍與生效時間仍未定案。台灣設備與晶圓代工相關公司已開始評估客戶組合與訂單影響。",
    date: "2026-07-16", occurred_at_iso: "2026-07-16T09:30:00+08:00", occurred_at_text: "今天 09:30",
    status: "developing", categories: ["政策", "關稅"], stars: 5, market_validation: 74, verify_state: "preliminary",
    source_count: 12, related_tickers: [{ ticker: "2330", name: "台積電" }, { ticker: "3034", name: "聯詠" }],
    image: { url: "https://images.unsplash.com/photo-1486406146926-c627a92ad1ab?auto=format&fit=crop&w=1400&q=86", alt: "玻璃帷幕商業建築仰角", credit: "Unsplash・示意影像", source_url: "https://unsplash.com/s/photos/policy-business" },
    importance_reasons: ["政策可能改變供應鏈訂單", "涉及多家大型半導體公司", "目前仍有關鍵細節待確認"],
    sources: [
      source("anue", "美國半導體設備限制傳擴大，亞洲供應鏈盤中震盪", "2026-07-16T09:30:00+08:00"),
      source("udn", "出口管制再升級？業者等待正式條文", "2026-07-16T09:42:00+08:00", true),
      source("ctee", "設備與成熟製程首當其衝，法人列三觀察點", "2026-07-16T10:05:00+08:00"),
    ],
    market_reaction: market("2330", [101.6, 100.8, 100, 98.7, 97.9, 98.6, 99.1], [-0.013, -0.014, null], 1.42, -2),
    timeline: [{ date: "07/12", title: "外媒首次披露政策方向" }, { date: "07/16", title: "限制範圍傳出擴大", current: true }],
  },
  {
    event_id: "evt_20260716_2317_003",
    title: "鴻海 6 月營收優於預期，AI 伺服器出貨成為主動能",
    deck: "營收數字確認出貨節奏，但市場仍在等待毛利率能否同步改善。",
    summary: "鴻海公布 6 月營收，AI 伺服器與雲端網路產品成長抵銷部分消費電子波動。市場下一步將觀察高營收是否能轉化為獲利率提升。",
    date: "2026-07-16", occurred_at_iso: "2026-07-16T15:05:00+08:00", occurred_at_text: "今天 15:05",
    status: "official_confirmed", categories: ["營收", "AI/科技"], stars: 4, market_validation: null, verify_state: "observing",
    source_count: 6, related_tickers: [{ ticker: "2317", name: "鴻海" }],
    image: { url: "https://images.unsplash.com/photo-1562408590-e32931084e23?auto=format&fit=crop&w=1400&q=86", alt: "現代自動化工廠生產線", credit: "Unsplash・示意影像", source_url: "https://unsplash.com/s/photos/factory" },
    importance_reasons: ["官方營收已公告", "AI 伺服器供應鏈代表公司", "尚待市場形成完整反應"],
    sources: [source("mops", "鴻海 6 月營收公告", "2026-07-16T15:00:00+08:00", true), source("anue", "鴻海營收優預期，AI 伺服器續強", "2026-07-16T15:08:00+08:00"), source("udn", "AI 出貨推升鴻海營收，毛利率成下個焦點", "2026-07-16T15:21:00+08:00", true)],
    market_reaction: market("2317", [99.2, 99.7, 100, 100.6, 101.1, 101.5, 101.2], [0.006, null, null], 1.2, 1),
    timeline: [{ date: "07/05", title: "公告月營收發布日期" }, { date: "07/16", title: "6 月營收公布", current: true }],
  },
  {
    event_id: "evt_20260716_shipping_004",
    title: "歐洲航線運價連三週上漲，市場重新評估第三季獲利",
    deck: "港口壅塞與繞道效應延長，航商短期報價獲得支撐。",
    summary: "最新航運指數顯示歐洲線運價連續第三週上漲，主因部分港口壅塞與船舶繞道。法人上修短期運價假設，但提醒新增運力仍可能限制漲勢。",
    date: "2026-07-16", occurred_at_iso: "2026-07-16T11:20:00+08:00", occurred_at_text: "今天 11:20",
    status: "market_reacting", categories: ["產業供需"], stars: 4, market_validation: 63, verify_state: "preliminary",
    source_count: 5, related_tickers: [{ ticker: "2603", name: "長榮" }],
    image: { url: "https://images.unsplash.com/photo-1586528116311-ad8dd3c8310d?auto=format&fit=crop&w=1400&q=86", alt: "貨櫃堆場與國際物流", credit: "Unsplash・示意影像", source_url: "https://unsplash.com/s/photos/container-shipping" },
    importance_reasons: ["運價影響航商獲利預期", "市場已出現量價反應", "供需變化仍可能快速反轉"],
    sources: [source("ctee", "歐洲線運價連三升，航商第三季旺季可期", "2026-07-16T10:58:00+08:00"), source("anue", "港口壅塞推升運價，貨櫃三雄走強", "2026-07-16T11:20:00+08:00"), source("udn", "運價上漲但新船壓力仍在，法人看法分歧", "2026-07-16T11:46:00+08:00", true)],
    market_reaction: market("2603", [97.8, 98.5, 100, 102.1, 103.4, 102.8, 103.1], [0.021, 0.028, null], 1.56, 3),
    timeline: [{ date: "07/02", title: "港口等待時間開始上升" }, { date: "07/16", title: "歐洲線運價連三週上漲", current: true }],
  },
  {
    event_id: "evt_20260716_power_005",
    title: "電價審議啟動，製造業成本與綠電需求同步受關注",
    deck: "尚未形成正式決議，但高用電產業已開始估算成本敏感度。",
    summary: "電價審議程序啟動，市場關注工業用電是否調整，以及企業綠電採購是否加速。現階段僅能確認議程，尚不能把傳聞中的調幅視為定案。",
    date: "2026-07-16", occurred_at_iso: "2026-07-16T08:40:00+08:00", occurred_at_text: "今天 08:40",
    status: "developing", categories: ["政策", "產業供需"], stars: 4, market_validation: 51, verify_state: "preliminary",
    source_count: 7, related_tickers: [{ ticker: "1519", name: "華城" }],
    image: { url: "https://images.unsplash.com/photo-1466611653911-95081537e5b7?auto=format&fit=crop&w=1400&q=86", alt: "山丘上的風力發電機", credit: "Unsplash・示意影像", source_url: "https://unsplash.com/s/photos/wind-energy" },
    importance_reasons: ["影響範圍跨越多個製造業", "政策仍在審議", "電力設備與綠電題材可能同步反應"],
    sources: [source("gov", "電價費率審議會議程公告", "2026-07-16T08:10:00+08:00", true), source("ctee", "電價審議啟動，高用電產業先算成本", "2026-07-16T08:40:00+08:00"), source("udn", "電價與綠電需求牽動製造業布局", "2026-07-16T09:12:00+08:00")],
    market_reaction: market("1519", [100.4, 99.8, 100, 100.7, 101.4, 101.1, 100.9], [0.007, 0.011, null], 1.09, 1),
    timeline: [{ date: "07/10", title: "主管機關確認審議時程" }, { date: "07/16", title: "產業開始評估成本", current: true }],
  },
  {
    event_id: "evt_20260716_2454_006",
    title: "聯發科新一代邊緣 AI 晶片進入量產，品牌客戶下半年導入",
    deck: "新品正式量產，後續觀察終端需求與產品組合對毛利率的貢獻。",
    summary: "聯發科宣布新一代邊緣 AI 晶片進入量產，數家品牌客戶預計下半年推出產品。由於實際出貨規模尚未揭露，市場目前以初步反應為主。",
    date: "2026-07-16", occurred_at_iso: "2026-07-16T13:40:00+08:00", occurred_at_text: "今天 13:40",
    status: "official_confirmed", categories: ["AI/科技", "產品"], stars: 4, market_validation: 67, verify_state: "preliminary",
    source_count: 5, related_tickers: [{ ticker: "2454", name: "聯發科" }], image: null,
    importance_reasons: ["產品已正式量產", "影響下半年產品組合", "出貨規模尚待後續驗證"],
    sources: [source("mops", "聯發科新產品量產相關公告", "2026-07-16T13:20:00+08:00", true), source("anue", "聯發科邊緣 AI 晶片量產，品牌客戶下半年導入", "2026-07-16T13:40:00+08:00"), source("ctee", "邊緣 AI 新品進量產，市場看毛利率貢獻", "2026-07-16T14:05:00+08:00")],
    market_reaction: market("2454", [99.1, 99.6, 100, 101.2, 102.4, 102.1, 102.8], [0.012, 0.021, null], 1.32, 2),
    timeline: [{ date: "06/28", title: "品牌客戶完成初步驗證" }, { date: "07/16", title: "新晶片進入量產", current: true }],
  },
  {
    event_id: "evt_20260716_bank_007",
    title: "公股金控上半年獲利創高，債券評價回升成共同推力",
    deck: "銀行本業穩定，市場開始比較各家資產品質與下半年股利能力。",
    summary: "多家公股金控公布上半年獲利，利息淨收益維持穩定，債券評價回升提供額外貢獻。投資人下一步將聚焦信用成本與股利政策。",
    date: "2026-07-16", occurred_at_iso: "2026-07-16T12:15:00+08:00", occurred_at_text: "今天 12:15",
    status: "official_confirmed", categories: ["財報", "金融"], stars: 3, market_validation: 58, verify_state: "preliminary",
    source_count: 4, related_tickers: [{ ticker: "2886", name: "兆豐金" }], image: null,
    importance_reasons: ["多家公司同步公布", "反映金融業上半年經營狀態", "個別公司差異仍需拆開檢視"],
    sources: [source("mops", "公股金控上半年自結獲利公告", "2026-07-16T11:50:00+08:00", true), source("udn", "公股金控獲利創高，債券評價回升", "2026-07-16T12:15:00+08:00"), source("ctee", "銀行本業穩健，法人轉看股利能力", "2026-07-16T12:42:00+08:00")],
    market_reaction: market("2886", [99.7, 99.9, 100, 100.5, 100.9, 101.1, 101.0], [0.005, 0.011, null], 0.94, 2),
    timeline: [{ date: "07/08", title: "6 月自結數字陸續公布" }, { date: "07/16", title: "上半年獲利完成彙整", current: true }],
  },
  {
    event_id: "evt_20260716_bio_008",
    title: "新藥二期試驗達主要指標，公司將申請與主管機關諮詢",
    deck: "臨床數據正向，但樣本規模與後續試驗設計仍是估值關鍵。",
    summary: "公司公告新藥二期試驗達成主要療效指標，並規劃與主管機關討論下一階段試驗。由於完整數據尚未公開，現階段不能直接推論最終核准機率。",
    date: "2026-07-16", occurred_at_iso: "2026-07-16T10:35:00+08:00", occurred_at_text: "今天 10:35",
    status: "official_confirmed", categories: ["產品", "生技"], stars: 3, market_validation: null, verify_state: "observing",
    source_count: 3, related_tickers: [{ ticker: "6488", name: "環球晶" }], image: null,
    importance_reasons: ["公司公告試驗結果", "仍需等待完整數據", "市場反應尚未形成"],
    sources: [source("mops", "二期臨床試驗結果重大訊息", "2026-07-16T10:20:00+08:00", true), source("anue", "新藥二期達標，公司規劃法規諮詢", "2026-07-16T10:35:00+08:00")],
    market_reaction: market("6488", [100.1, 99.8, 100, 100.2, 100.4, 100.3, 100.5], [0.002, null, null], 0.88, 0),
    timeline: [{ date: "04/12", title: "完成最後一位受試者收案" }, { date: "07/16", title: "公告二期主要指標", current: true }],
  },
  {
    event_id: "evt_20260715_twse_009",
    title: "證交所調整處置制度資訊揭露，年底前完成系統改版",
    deck: "規則本身不改變交易限制，但投資人將更早看到風險提示。",
    summary: "證交所宣布調整處置制度的資訊揭露方式，新增更清楚的風險提示與查詢入口。相關系統預計年底前上線，交易限制標準暫不調整。",
    date: "2026-07-15", occurred_at_iso: "2026-07-15T17:10:00+08:00", occurred_at_text: "昨天 17:10",
    status: "official_confirmed", categories: ["政策"], stars: 3, market_validation: 45, verify_state: "preliminary",
    source_count: 4, related_tickers: [{ ticker: "TWSE", name: "臺灣證券交易所" }], image: null,
    importance_reasons: ["正式制度資訊更新", "影響所有市場參與者", "不直接改變交易規則"],
    sources: [source("twse", "處置制度資訊揭露改版說明", "2026-07-15T16:50:00+08:00", true), source("udn", "證交所改善處置資訊，年底前上線", "2026-07-15T17:10:00+08:00")],
    market_reaction: null,
    timeline: [{ date: "06/30", title: "提出資訊揭露改善方向" }, { date: "07/15", title: "正式公布改版時程", current: true }],
  },
  {
    event_id: "evt_20260715_memory_010",
    title: "記憶體現貨價續揚，模組廠庫存水位降至健康區間",
    deck: "價格與庫存同時改善，但下游補庫能否延續仍待月底數據。",
    summary: "記憶體現貨價延續漲勢，部分模組廠庫存水位回到健康區間。市場關注伺服器需求是否能抵銷消費性產品的季節波動。",
    date: "2026-07-15", occurred_at_iso: "2026-07-15T13:20:00+08:00", occurred_at_text: "昨天 13:20",
    status: "market_reacting", categories: ["產業供需", "AI/科技"], stars: 3, market_validation: 71, verify_state: "verified",
    source_count: 6, related_tickers: [{ ticker: "2344", name: "華邦電" }], image: null,
    importance_reasons: ["報價與庫存同向改善", "市場已有量價反應", "月底需求數據仍是下一驗證點"],
    sources: [source("ctee", "記憶體現貨價續漲，模組庫存改善", "2026-07-15T13:02:00+08:00"), source("anue", "記憶體族群走強，市場押注補庫延續", "2026-07-15T13:20:00+08:00"), source("udn", "伺服器需求撐盤，消費性旺季仍待觀察", "2026-07-15T13:48:00+08:00", true)],
    market_reaction: market("2344", [98.4, 99.2, 100, 102.4, 103.1, 103.7, 103.5], [0.024, 0.037, null], 1.63, 3),
    timeline: [{ date: "07/01", title: "現貨價止跌" }, { date: "07/15", title: "報價連續第三週上漲", current: true }],
  },
];

export const eventsFixture: EventCatalog = {
  meta: {
    edition_date: "2026-07-16",
    as_of: "2026-07-16T16:20:00+08:00",
    total_events: events.length,
    total_sources: events.reduce((sum, event) => sum + event.source_count, 0),
  },
  categories: ["全部", "法說", "財報", "政策", "AI/科技", "產業供需", "營收", "金融", "生技"],
  events,
};
