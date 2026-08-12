"""控制層請求身分與反攻防硬界線的唯一真實來源。

**做（瀏覽器等級隱身）**：真實瀏覽器 User-Agent 字串 + 相容 headers（Accept /
Accept-Language / sec-ch-ua 等），並留一條誠實痕跡 header ``X-Purpose``
（課程練習、非商業）。低速由 AutoThrottle 維持。

**不做（硬界線，寫死在這裡當契約）**：
- 代理輪替 / 隨機 UA 池（本檔只有一個固定 UA 字串，不做輪替）
- TLS/JA3 指紋偽裝、瀏覽器指紋 patch
- CAPTCHA 解題
- 冒充特定真實個人或機構

硬被 WAF 擋 = KILL 訊號 → feasibility_triage/diagnose 走死信，不打軍備競賽。
付費牆 / CAPTCHA 一律不繞（policy_kill）。
"""

from __future__ import annotations

# 課程練習誠實痕跡：低調自訂 header，讓對方站方能看出這不是商業爬蟲。
COURSE_PURPOSE = "academic course exercise, non-commercial"

# 單一固定、真實的桌面 Chrome UA 字串（不是 UA 池、不做輪替——見上方硬界線）。
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# 與上述 UA 相容的標準瀏覽器 headers（含 X-Purpose 誠實痕跡）。
BROWSER_HEADERS: dict[str, str] = {
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "application/json;q=0.8,*/*;q=0.7"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Upgrade-Insecure-Requests": "1",
    "X-Purpose": COURSE_PURPOSE,
}


def browser_request_headers() -> dict[str, str]:
    """給 requests / Scrapy 用的完整請求 headers（UA + 相容 headers + X-Purpose）。"""
    return {"User-Agent": BROWSER_USER_AGENT, **BROWSER_HEADERS}
