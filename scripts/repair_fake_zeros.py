"""假零值深度救援 CLI——邏輯在 eventsignal/finmind/repair.py（每日批次共用同一套）。

  python scripts/repair_fake_zeros.py            # dry-run：只掃描
  python scripts/repair_fake_zeros.py --apply    # 重抓仲裁並修復
  python scripts/repair_fake_zeros.py --days 14  # 掃描窗（預設 30 天）

當天的自動仲裁已內建於 daily_batch 收尾；本 CLI 用於清多日存量。
⚠️ 與每日批次共用 FinMind 額度——VM 管線跑的時候別跑 --apply。
"""
import argparse

from eventsignal.core import config
from eventsignal.finmind.client import FinMindClient
from eventsignal.finmind.repair import arbitrate, scan_zero_rows


def main() -> None:
    ap = argparse.ArgumentParser(description="假零值掃描與救援")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    config.load_env()
    import psycopg
    with psycopg.connect(config.require("DATABASE_URL")) as conn:
        by_tk = scan_zero_rows(conn, args.days)
        n = sum(len(v) for v in by_tk.values())
        print(f"近 {args.days} 天全零列：{n} 列、{len(by_tk)} 檔")
        if not args.apply:
            print("（dry-run 結束；--apply 執行仲裁。VM 管線跑的時候別跑，額度共用。）")
            return
        token = config.get("FINMIND_TOKEN") or ""
        client = FinMindClient("".join(token.split()))   # token 曾混入換行，見記憶
        ok0, fx, sk = arbitrate(conn, client, by_tk)
        print(f"仲裁完成：真零確認 {ok0} 列、假零修復 {fx} 列、跳過 {sk} 檔")
        if fx:
            print("⚠️ 未定案事件的驗證分將由每日 rescore 自動更新；"
                  "已 verified 的事件如需重評請另行處理。")


if __name__ == "__main__":
    main()
