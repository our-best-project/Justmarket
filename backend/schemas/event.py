#04_API_v2#3事件主對照表（events)-coding
#將事件主對照表轉化成JSON(BaseModel)

from __future__ import annotations

from datetime import date, datetime  #預設日期時間
from enum import Enum

from pydantic import BaseModel, Field

# Enum設定

# 事件消息面方向
class ExpectedDirection(str, Enum):
    bullish = '利多'
    bearish = '利空'
    neutral = '中性'

# 市場驗證狀態
class VerifyState(str, Enum):
    observing = 'observing'         #觀察中
    preliminary = 'preliminary'     #初步的
    verified = 'verified'           #已驗證

# 消息確認度（前端顯示）對應Neon的status
class EventStatus(str, Enum):
    official_confirmed = 'official_confirmed'  
    rumor_unconfirmed = 'rumor_unconfirmed'
    developing = 'developing'
    market_reacting = 'market_reacting'

# 單條籌碼證據指向
class ChipDirection(str, Enum):
    long ='看多'
    short = '看空'
    neutral ='中性'


# 低時市場驗證略過一致性閘門
class DirectionConfidence(str, Enum):
    high = 'high'
    low = 'low'


# BaseModel設定 子物件
class ChipEvidence(BaseModel):
    label: str
    detail: str
    direction: ChipDirection

class ValidationBreakdown(BaseModel):
    foreign: str
    investment_trust: str
    volume: str
    price: str
    divergence: bool #bool為布林值,ex: "divergence": false.

# 根據04_API_v2-API回傳名:Summary
# 故事A/D/E的格式
class EventSummary(BaseModel):
    event_id: str
    title: str
    source_count: int
    related_tickers: list[str] #ex:["2330"] 一串字串需用list
    occurred_at_iso: datetime
    occurred_at_text: str
    categories: list[str] #ex:["法說","財報"] 一串字串需用list
    stars : int = Field(ge=1, le=5) #對應Neon型別:smallint,CHECK (importance_stars >= 1 AND importance_stars <= 5)
    
    #對應Neon型別:smallint, CHECK (market_validation >= 0 AND market_validation <= 100),None ->允許null存在,default這個值是為了市場驗證沒給分的情況下加上去讓網頁不報錯
    market_validation : int | None  = Field(default=None, ge=0, le=100)
    status: EventStatus
    verify_state: VerifyState

# 根據04_API_v2-API回傳名:Detail -故事B格式
class EventDetail(EventSummary):
    summary: str
    expected_direction: ExpectedDirection
    direction_confidence: DirectionConfidence
    confidence_note: str
    importance_reasons: list[str]
    # validation_breakdown: ValidationBreakdown
    validation_breakdown: ValidationBreakdown | None = None
    chip_evidence: list[ChipEvidence]
    source_links: list[str]


# 時間線的一個節點(一列) -故事C格式
class TimelineNode(BaseModel):
    date: date
    title: str
    event_id: str
    # is_current: bool = None pydantic v2會報錯
    is_current: bool | None = None

# 整個時間軸(一個總框,裝許多串節點)
class Timeline(BaseModel):
    event_id: str
    timeline: list[TimelineNode]

#影響擴散方式 故事F格式(依據契約#5 輔助型別)
class ImpactItem(BaseModel):
    ticker: str
    name: str
    relation: str #受惠/受害的事件數量
    role: str 
    degree: str #高/中/低(同上)

class Impact(BaseModel):
    event_id: str
    impacts: list[ImpactItem] 

# Ticker-故事D格式
class TickerHint(BaseModel):
    ticker: str
    name: str
