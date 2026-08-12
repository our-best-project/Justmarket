"""每日管線調度器：步驟清單活在版控裡，殼腳本再也不能漏步驟。

【為什麼需要】run_daily.sh 曾漏掉向量化步驟（bge_m3）——殼腳本的步驟清單
沒有版控審查、沒有測試守護，VM 上的副本與 repo 漂移了也沒人發現，結果
②③ 每輪空轉、pending 無限堆積（2026-08-11 事故）。
把「跑哪些步驟、什麼順序」收進這裡：tests/test_daily_steps.py 直接斷言
步驟完整性——再漏一步，CI 就紅。

【設計】
- 每步驟一個 subprocess（python -m ...）：單步崩潰不拖垮整輪，與殼腳本
  時代行為一致，但失敗會被記錄並反映在 exit code。
- 步驟間沒有隱藏依賴注入——全靠 DB 的 status 交接棒，天生可重跑。
- 收尾必跑健康記分板（pipeline_health）：空轉不能再裝成功。

  python -m eventsignal.daily            # 跑整輪
  python -m eventsignal.daily --list     # 只列步驟（給人與測試看）
"""
import argparse
import subprocess
import sys
import time

# 步驟順序即交接棒順序：pending → vectorized → clustered → summarized → scored
# ⚠️ 改這張表請同步看 tests/test_daily_steps.py 的守護測試
STEPS: list[tuple[str, list[str]]] = [
    ("① 爬蟲（8 來源增量）",   ["-m", "eventsignal.ingestion", "--all"]),
    ("② 向量化（pending→vectorized）", ["-m", "eventsignal.embedding.bge_m3"]),
    ("③ 分群（vectorized→clustered）", ["-m", "eventsignal.pipeline", "--stages", "embedding,clustering"]),
    ("④ 籌碼（FinMind 需求驅動＋假零仲裁）", ["-m", "eventsignal.finmind.daily_batch", "--demand-only"]),
    ("④b 大盤指數（8 國日線）", ["-m", "eventsignal.market_index.daily_batch"]),
    ("⑤ 篩選＋LLM＋評分",      ["-m", "eventsignal.pipeline", "--stages", "llm,scoring"]),
    ("⑥ 健康記分板",           ["-m", "eventsignal.pipeline_health"]),
]


def main() -> None:
    ap = argparse.ArgumentParser(description="每日管線調度器")
    ap.add_argument("--list", action="store_true", help="只列步驟不執行")
    args = ap.parse_args()

    if args.list:
        for name, cmd in STEPS:
            print(f"{name:<28} python {' '.join(cmd)}")
        return

    failures: list[str] = []
    t_all = time.time()
    for name, cmd in STEPS:
        print(f"\n──── {name} 開始 ────", flush=True)
        t0 = time.time()
        rc = subprocess.run([sys.executable, "-u", *cmd]).returncode
        mins = (time.time() - t0) / 60
        # 健康記分板的非零 exit 是「有警告」不是「步驟失敗」，分開記
        if rc != 0 and "pipeline_health" not in cmd[1]:
            failures.append(f"{name}（rc={rc}）")
            print(f"──── {name} 失敗 rc={rc}（{mins:.1f} 分）——續跑後續步驟 ────", flush=True)
        else:
            print(f"──── {name} 完成（{mins:.1f} 分） ────", flush=True)

    total = (time.time() - t_all) / 60
    print(f"\n════ 每日管線結束：{total:.0f} 分鐘"
          + (f"，失敗步驟：{'、'.join(failures)}" if failures else "，全部步驟完成") + " ════")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
