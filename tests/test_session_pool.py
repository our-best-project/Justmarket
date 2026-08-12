"""app/db/session.py 連線池的單元測試。不連任何資料庫。"""

import inspect
from contextlib import contextmanager

import pytest

from backend.db import session as db_session


class FakePool:
    """替身池：記錄建立參數與開關，不做任何網路動作。"""

    instances: list["FakePool"] = []

    def __init__(self, conninfo, **kwargs):
        self.conninfo = conninfo
        self.kwargs = kwargs
        self.closed = False
        FakePool.instances.append(self)

    @staticmethod
    def check_connection(conn):
        return None

    @contextmanager
    def connection(self):
        yield f"conn-from-pool-{len(FakePool.instances)}"

    def close(self):
        self.closed = True


@pytest.fixture
def fake_pool(monkeypatch):
    FakePool.instances = []
    monkeypatch.setattr(db_session, "_connection_pool_class", lambda: FakePool)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/testdb")
    db_session.close_pool()          # 起點乾淨
    yield FakePool
    db_session.close_pool()          # 不把池洩漏給下一個測試


def test_open_pool_is_idempotent(fake_pool):
    db_session.open_pool()
    db_session.open_pool()
    assert len(fake_pool.instances) == 1


def test_close_pool_without_open_is_noop():
    db_session.close_pool()
    db_session.close_pool()          # 不應拋例外


def test_close_pool_closes_the_pool(fake_pool):
    db_session.open_pool()
    pool = fake_pool.instances[0]
    db_session.close_pool()
    assert pool.closed is True


def test_get_pooled_conn_yields_from_pool(fake_pool):
    with db_session.get_pooled_conn() as conn:
        assert str(conn).startswith("conn-from-pool-")


def test_get_pooled_conn_opens_pool_lazily(fake_pool):
    """端點函式在測試中可獨立呼叫，不必先跑 app 啟動流程。"""
    assert fake_pool.instances == []
    with db_session.get_pooled_conn():
        pass
    assert len(fake_pool.instances) == 1


def test_pool_keeps_dict_row_factory(fake_pool):
    """現有端點全部依賴 dict 形式的查詢結果，這個設定掉了會全線壞掉。"""
    from psycopg.rows import dict_row

    db_session.open_pool()
    assert fake_pool.instances[0].kwargs["kwargs"]["row_factory"] is dict_row


def test_existing_names_are_untouched():
    """守住承諾：非 API 模組 import 的兩個名字不得改變。

    （pipeline / ingestion / crawler_legacy.mops_crawler / embedding.bge_m3 /
      market_index.daily_batch / orchestration.flows）

    第三個名字 `_load_dotenv` 曾在本檔，2026-08-08 收斂到 core/config 後移除；
    連線池是純加法，沒有動到這兩個。
    """
    for name in ("db_url", "get_conn"):
        assert callable(getattr(db_session, name)), f"{name} 不見了"
    assert list(inspect.signature(db_session.get_conn).parameters) == []
    assert list(inspect.signature(db_session.db_url).parameters) == []
