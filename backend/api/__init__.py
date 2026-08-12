"""② API 層：每個檔案一組端點，全部在 main.py 掛上 /api/v1。

    events.py   契約端點（故事 A/B/C/E）
    tickers.py  契約端點（故事 D）
    demo.py     frontend/ 前端專用：一次撈完首頁要的東西
    market.py   大盤脈搏（目前固定值）

回傳形狀定義在 app/schemas/event.py，不在這裡。
"""
