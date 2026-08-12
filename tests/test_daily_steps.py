"""每日管線步驟清單的守護測試——針對真實事故寫的回歸。

2026-08-11 事故：run_daily.sh 漏掉向量化步驟（bge_m3），②③ 每輪空轉、
pending 無限堆積至 3,000+，而每輪 log 照樣印「管線結束」。
步驟清單移進 eventsignal/daily.py 後，本測試斷言它的完整性與順序——
再有人（包括未來的自己）動掉一步，這裡先紅。
"""
import sys

from eventsignal.daily import STEPS

MODULES = [cmd[1] for _, cmd in STEPS]


def test_all_stages_present():
    """五段管線＋指數＋健檢，一個都不能少。"""
    required = [
        "eventsignal.ingestion",
        "eventsignal.embedding.bge_m3",       # ← 事故主角：真正的向量化
        "eventsignal.pipeline",                          # 分群
        "eventsignal.finmind.daily_batch",
        "eventsignal.market_index.daily_batch",
        "eventsignal.pipeline_health",
    ]
    for module in required:
        assert module in MODULES, f"每日管線缺步驟：{module}"


def test_vectorize_before_clustering():
    """bge_m3（產生 vectorized）必須在分群之前——顛倒＝分群永遠空轉。"""
    assert MODULES.index("eventsignal.embedding.bge_m3") < MODULES.index("eventsignal.pipeline")


def test_health_check_is_last():
    """健檢必須最後跑——它驗證的是「整輪跑完之後」的不變量。"""
    assert MODULES[-1] == "eventsignal.pipeline_health"


def test_llm_after_chip_data():
    """⑤ 在 ④ 之後：市場驗證重評需要當日籌碼先落庫。"""
    finmind = MODULES.index("eventsignal.finmind.daily_batch")
    llm = max(i for i, m in enumerate(MODULES) if m == "eventsignal.pipeline")
    assert finmind < llm


if __name__ == "__main__":
    failed = 0
    for name in list(globals()):
        if name.startswith("test_"):
            try:
                globals()[name]()
                print("PASS", name)
            except AssertionError as exc:
                failed += 1
                print("FAIL", name, exc)
    sys.exit(1 if failed else 0)
