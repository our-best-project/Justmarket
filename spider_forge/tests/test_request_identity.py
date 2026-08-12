"""請求身分與反攻防硬界線。

驗收重點：
- 瀏覽器等級 UA + 相容 headers + X-Purpose 誠實痕跡（做的部分）。
- recon 的 _fetch_sample 以瀏覽器身分抓取（§3.1 校準）。
- 硬界線：只有單一固定 UA（非 UA 池），不做代理/指紋/CAPTCHA。

跑法（從 /）：
    python -m spider_forge.tests.test_request_identity
"""

from __future__ import annotations

from spider_forge.shared import request_identity
from spider_forge.shared.prompts import _SPIDER_CONTRACT


def t_browser_headers_are_real_and_honest():
    headers = request_identity.browser_request_headers()
    ok = (
        "Chrome/" in headers["User-Agent"]
        and headers["User-Agent"].startswith("Mozilla/5.0")
        and headers["X-Purpose"] == "academic course exercise, non-commercial"
        and headers["Accept-Language"].startswith("zh-TW")
        and "sec-ch-ua" in headers
    )
    return ok, f"ua={headers['User-Agent'][:40]}... x_purpose={headers.get('X-Purpose')}"


def t_single_fixed_ua_not_a_pool():
    """硬界線：只有一個固定 UA 字串，不做 UA 池/輪替。"""
    ok = (
        isinstance(request_identity.BROWSER_USER_AGENT, str)
        and "\n" not in request_identity.BROWSER_USER_AGENT
    )
    # 請求身分模組不應暴露任何「UA 清單/池」樣式的容器
    pool_like = [
        name
        for name in dir(request_identity)
        if not name.startswith("_")
        and isinstance(
            getattr(request_identity, name), (list, tuple, set)
        )
        and any(
            "mozilla" in str(x).lower()
            for x in getattr(request_identity, name)
        )
    ]
    return ok and not pool_like, f"single_ua={ok} pool_like={pool_like}"


def t_fetch_sample_sends_browser_identity():
    import requests

    import spider_forge.shared.evidence as evidence

    captured = {}

    class FakeResp:
        status_code = 200
        headers = {"content-type": "text/html; charset=utf-8"}
        text = "<html><head></head><body>ok</body></html>"
        url = "https://example.com/news"
        history: list = []

    def fake_get(url, **kwargs):
        captured["headers"] = kwargs.get("headers") or {}
        return FakeResp()

    original = requests.get
    requests.get = fake_get
    try:
        evidence._fetch_sample("https://example.com/news")
    finally:
        requests.get = original
    sent = captured.get("headers", {})
    ok = (
        "Chrome/" in sent.get("User-Agent", "")
        and sent.get("X-Purpose") == "academic course exercise, non-commercial"
    )
    return ok, f"ua={sent.get('User-Agent', '')[:30]} x_purpose={sent.get('X-Purpose')}"


def t_generator_contract_forbids_bypassing_paywall_or_captcha():
    contract = _SPIDER_CONTRACT
    ok = (
        "付費牆" in contract
        and "CAPTCHA" in contract
        and "不繞" in contract
        and "ROBOTSTXT_OBEY" not in contract
    )
    return ok, f"has_paywall_line={'付費牆' in contract}"


TESTS = [
    t_browser_headers_are_real_and_honest,
    t_single_fixed_ua_not_a_pool,
    t_fetch_sample_sends_browser_identity,
    t_generator_contract_forbids_bypassing_paywall_or_captcha,
]


def main() -> int:
    failed = 0
    for test in TESTS:
        try:
            ok, detail = test()
        except Exception as exc:
            ok, detail = False, f"EXCEPTION {exc}"
        print(f"[{'PASS' if ok else 'FAIL'}] {test.__name__}: {detail}")
        failed += not ok
    print(f"\n{len(TESTS) - failed}/{len(TESTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
