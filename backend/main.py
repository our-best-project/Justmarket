"""FastAPI 應用入口（② API 層，只讀）。

啟動：`python -m backend api`（等價於 uvicorn backend.main:app）
驗收：http://localhost:8000/docs 看得到全部端點、Try it out 回得出 JSON。

只讀的意思是字面的：分數全部在 ④ 處理層算好寫進 DB，這一層不運算也不寫入，只有 GET。
"""
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.api import demo, events, market, tickers
from backend.db.session import close_pool, open_pool


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """API 進程的 DB 連線池生命週期（見 app/db/session.py 的連線池段）。

    啟動就開池 —— DATABASE_URL 沒設或 DB 連不上會在這裡直接失敗，而不是等到
    第一個請求進來才炸。原本是後者：錯誤散在每個請求裡，看起來像 API 壞了，
    實際上是設定沒填。
    """
    open_pool()
    yield
    close_pool()


app = FastAPI(
    title="EventSignal API",
    description="台股事件分析平台 只讀 API（契約：0630_MVP規格/04_API_v2.md）",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS：本機開發時前端在別的 port，串接日最常見的錯就是沒開這個。
#   5173 = vite dev（web/vite.config.ts）
#   4173 = vite preview（同檔）
# 部署到 GitHub Pages 時前端變成另一個網域，必須用 CORS_EXTRA_ORIGINS 補進來，
# 例如 CORS_EXTRA_ORIGINS=https://our-best-project.github.io
# 寫成環境變數而不是寫死：同一個映像檔要能同時服務 Pages 與本機，網址變動不該重 build。
_LOCAL_ORIGINS = [
    "http://localhost:5173", "http://127.0.0.1:5173",
    "http://localhost:4173", "http://127.0.0.1:4173",
]
_EXTRA_ORIGINS = [o.strip() for o in os.environ.get("CORS_EXTRA_ORIGINS", "").split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=[*_LOCAL_ORIGINS, *_EXTRA_ORIGINS],
    allow_methods=["GET"],          # API 只讀，只開 GET
    allow_headers=["*"],
)

# 事件示意圖：檔案放 backend/static/images/，不進資料庫。
# 對應 services/image_picker.py 的 URL_PREFIX = "/static/images"。
# 注意這個 mount 不在 /api/v1 底下 —— 圖片是靜態資源，不是契約端點。
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

# base_url = /api/v1
app.include_router(events.router, prefix="/api/v1")
app.include_router(tickers.router, prefix="/api/v1")
# /demo/*：前端 web/ 實際使用的那組（一次載入、之後純前端切換）。
# 與上面 events/tickers 的分離式契約是兩套架構，並存不互相取代。
app.include_router(demo.router, prefix="/api/v1")
app.include_router(market.router, prefix="/api/v1")


@app.get("/health", tags=["infra"])
def health():
    """給 docker compose healthcheck / 監控用。

    ⚠️ 附 DB readiness（P3-07）。原本只回靜態 ok——服務活著但 Neon 連不上時
    healthcheck 照樣全綠，監控完全看不出「活著卻端不出資料」的狀態。
    DB 掛掉回 503，compose/監控才會把它視為不健康。
    """
    from fastapi.responses import JSONResponse

    from backend.db.session import get_conn
    try:
        with get_conn() as conn:
            conn.execute("select 1")
        return {"status": "ok", "db": "ok"}
    except Exception as exc:
        return JSONResponse(status_code=503,
                            content={"status": "degraded", "db": str(exc)[:200]})


def _serve() -> None:
    """`python -m backend api` 的實作。

    等價於 `uvicorn backend.main:app --host 0.0.0.0 --port 8000`。
    host/port 走環境變數而非 flag —— 容器與 Cloud Run 都是用環境變數注入的
    （Cloud Run 會給 $PORT），多一套 flag 只會多一個對不上的地方。
    """
    import uvicorn

    uvicorn.run(
        "backend.main:app",
        host=os.environ.get("API_HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8000")),
        reload=os.environ.get("API_RELOAD") == "1",
    )
