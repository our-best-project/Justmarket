"""CORS 允許清單的守護測試。

為什麼值得一個測試：CORS 設錯的失敗方式特別安靜——API 回 200、log 乾淨、
`curl` 也完全正常，只有瀏覽器 console 會說話。正式站台一旦從清單裡掉出去，
整個前端取不到任何資料，而後端看起來一切健康。

不用 TestClient：那會觸發 lifespan 去開 DB 連線池，這裡要驗的東西跟 DB 無關。
"""
import os

from backend.main import DEFAULT_ORIGINS, app

PAGES_ORIGIN = "https://our-best-project.github.io"


def _cors_middleware():
    for middleware in app.user_middleware:
        if middleware.cls.__name__ == "CORSMiddleware":
            return middleware
    raise AssertionError("找不到 CORSMiddleware —— API 沒有掛 CORS，前端一定取不到資料")


def test_production_frontend_origin_is_allowed():
    """GitHub Pages 正式站台必須在預設清單裡，不能只靠環境變數。"""
    assert PAGES_ORIGIN in DEFAULT_ORIGINS


def test_local_dev_origins_are_allowed():
    """vite dev（5173）與 vite preview（4173）兩個 port 都要能跨來源呼叫。"""
    for port in (5173, 4173):
        assert f"http://localhost:{port}" in DEFAULT_ORIGINS
        assert f"http://127.0.0.1:{port}" in DEFAULT_ORIGINS


def test_middleware_actually_received_the_origins():
    """清單有寫是一回事，有沒有真的掛進 middleware 是另一回事。"""
    allowed = _cors_middleware().kwargs["allow_origins"]
    for origin in DEFAULT_ORIGINS:
        assert origin in allowed


def test_read_only_api_only_allows_get():
    """API 是只讀的；允許其他方法等於把不存在的寫入面攤開來。"""
    assert _cors_middleware().kwargs["allow_methods"] == ["GET"]


def test_extra_origins_come_from_env(monkeypatch):
    """CORS_EXTRA_ORIGINS 逗號分隔、去空白、忽略空項。"""
    monkeypatch.setenv("CORS_EXTRA_ORIGINS", " https://a.example , ,https://b.example ")
    parsed = [o.strip() for o in os.environ["CORS_EXTRA_ORIGINS"].split(",") if o.strip()]
    assert parsed == ["https://a.example", "https://b.example"]
