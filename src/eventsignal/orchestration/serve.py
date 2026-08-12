"""註冊並長駐服務三條 Prefect deployment。"""

from prefect import serve
from prefect.schedules import Cron

from eventsignal.orchestration.flows import (
    finmind_after_market,
    market_index_daily,
    news_daily,
)


def main() -> None:
    deployments = [
        news_daily.to_deployment(
            name="news-hourly-taipei",
            description="crawler 每小時新聞 → Neon → 事件管線",
            schedule=Cron("0 6-23 * * *", timezone="Asia/Taipei"),
            tags=["hourly", "crawler-runtime", "gpu"],
        ),
        market_index_daily.to_deployment(
            name="market-index-daily-taipei",
            description="八大指數最近收盤日線，每日台北 15:05",
            schedule=Cron("5 15 * * *", timezone="Asia/Taipei"),
            tags=["daily", "market-index"],
        ),
        finmind_after_market.to_deployment(
            name="finmind-after-market-taipei",
            description="FinMind 全量籌碼與事件市場驗證，每日台北 18:10",
            schedule=Cron("10 18 * * *", timezone="Asia/Taipei"),
            tags=["daily", "finmind"],
        ),
    ]
    serve(*deployments, pause_on_shutdown=False)


if __name__ == "__main__":
    main()
