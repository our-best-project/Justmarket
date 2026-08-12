"""事件級 LLM 回歸跑器：重播已知失效案例，檢查 prompt 或模型改動是否讓舊病復發。

用法:
    python -m backend.llm.regression --self-test  # 離線驗證四種檢查器
    python -m backend.llm.regression --dry        # 離線驗案例檔結構
    python -m backend.llm.regression run          # 呼叫 LLM 跑完整回歸
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.llm.summarize import (
    DEFAULT_SLEEP_SECONDS,
    enforce_official_guard,
    summarize_event,
)

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass


CASES_PATH = Path(__file__).with_name("golden") / "regression_cases.json"
EXPECT_KEYS = {
    "occurred_at_within_published",
    "related_tickers_allowed",
    "status_not",
    "expected_direction",
}
ARTICLE_FIELDS = ("source", "source_type", "published_at", "title", "content")
TAIPEI_TZ = timezone(timedelta(hours=8))


def load_cases(path: Path = CASES_PATH) -> object:
    """讀取回歸案例 JSON；結構是否合法由 validate_cases 統一回報。"""
    with path.open(encoding="utf-8") as file:
        return json.load(file)


def validate_cases(payload: object) -> tuple[list[dict], list[str]]:
    """驗證案例檔契約，回傳（可用案例，問題清單）。"""
    problems: list[str] = []
    if not isinstance(payload, dict):
        return [], ["根節點須為 JSON 物件"]
    cases = payload.get("cases")
    if not isinstance(cases, list):
        return [], ["根節點的 cases 須為陣列"]
    if not cases:
        return [], ["根節點的 cases 須為非空陣列"]

    seen_ids: set[str] = set()
    for index, case in enumerate(cases, 1):
        label = f"cases[{index}]"
        if not isinstance(case, dict):
            problems.append(f"{label} 須為物件")
            continue

        for field in ("case_id", "failure_mode"):
            if not isinstance(case.get(field), str) or not case[field].strip():
                problems.append(f"{label}.{field} 須為非空字串")
        case_id = case.get("case_id")
        if isinstance(case_id, str) and case_id:
            if case_id in seen_ids:
                problems.append(f"{label}.case_id 重複: {case_id!r}")
            seen_ids.add(case_id)

        articles = case.get("articles")
        if not isinstance(articles, list) or not articles:
            problems.append(f"{label}.articles 須為非空陣列")
        else:
            for article_index, article in enumerate(articles, 1):
                article_label = f"{label}.articles[{article_index}]"
                if not isinstance(article, dict):
                    problems.append(f"{article_label} 須為物件")
                    continue
                for field in ARTICLE_FIELDS:
                    if field not in article:
                        problems.append(f"{article_label} 缺少欄位: {field}")
                published_at = article.get("published_at")
                if "published_at" in article:
                    if not isinstance(published_at, str):
                        problems.append(f"{article_label}.published_at 須為 ISO 字串")
                    else:
                        try:
                            datetime.fromisoformat(published_at)
                        except ValueError:
                            problems.append(
                                f"{article_label}.published_at 無法解析: {published_at!r}"
                            )

        expect = case.get("expect")
        if not isinstance(expect, dict) or not expect:
            problems.append(f"{label}.expect 須為非空物件")
            continue
        illegal = sorted(set(expect) - EXPECT_KEYS)
        if illegal:
            problems.append(f"{label}.expect 含非法鍵: {illegal}")
        if "occurred_at_within_published" in expect:
            if expect["occurred_at_within_published"] is not True:
                problems.append(
                    f"{label}.expect.occurred_at_within_published 只能是 true"
                )
        if "related_tickers_allowed" in expect:
            allowed = expect["related_tickers_allowed"]
            if not isinstance(allowed, list) or any(
                not isinstance(ticker, str) for ticker in allowed
            ):
                problems.append(
                    f"{label}.expect.related_tickers_allowed 須為字串陣列"
                )
        for key in ("status_not", "expected_direction"):
            if key in expect and (
                not isinstance(expect[key], str) or not expect[key].strip()
            ):
                problems.append(f"{label}.expect.{key} 須為非空字串")

    return cases, problems


def _check_occurred_at(fields: dict, articles: list[dict]) -> tuple[bool, object, object]:
    actual = fields.get("occurred_at_iso")
    published_dates = []
    for article in articles:
        parsed_published = datetime.fromisoformat(article["published_at"])
        if parsed_published.tzinfo is None:
            parsed_published = parsed_published.replace(tzinfo=TAIPEI_TZ)
        published_dates.append(parsed_published.date())
    deadline = max(published_dates) + timedelta(days=1)
    expected = f"date <= {deadline.isoformat()}（或 null）"
    if actual is None:
        return True, None, expected
    try:
        parsed = datetime.fromisoformat(str(actual))
    except (TypeError, ValueError):
        return False, actual, expected
    # 與 prompts.validate_output 一致：台股新聞中的 naive 時間視為台北時間。
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TAIPEI_TZ)
    return parsed.date() <= deadline, actual, expected


def check_expectations(
    fields: dict, articles: list[dict], expect: dict
) -> list[dict]:
    """逐項檢查單一案例，回傳供摘要表使用的實際值、期望值與結果。"""
    checks: list[dict] = []
    for key, expected in expect.items():
        if key == "occurred_at_within_published":
            passed, actual, display_expected = _check_occurred_at(fields, articles)
        elif key == "related_tickers_allowed":
            actual = fields.get("related_tickers")
            passed = isinstance(actual, list) and set(actual) <= set(expected)
            display_expected = f"subset of {expected!r}"
        elif key == "status_not":
            actual = fields.get("status")
            passed = actual != expected
            display_expected = f"!= {expected!r}"
        else:
            actual = fields.get("expected_direction")
            passed = actual == expected
            display_expected = expected
        checks.append({
            "key": key,
            "passed": passed,
            "actual": actual,
            "expected": display_expected,
        })
    return checks


def _display(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def print_results(results: list[dict]) -> None:
    """印出每案例結果，以及每條規則的實際值與期望值。"""
    print("CASE\tRESULT\tCHECK\tACTUAL\tEXPECTED")
    for result in results:
        case_id = result["case_id"]
        if "error" in result:
            print(f"{case_id}\tFAIL\tERROR\t{_display(result['error'])}\t成功完成案例")
            continue
        status = "PASS" if result["passed"] else "FAIL"
        for check in result["checks"]:
            check_status = "PASS" if check["passed"] else "FAIL"
            print(
                f"{case_id}\t{status}\t{check['key']}:{check_status}\t"
                f"{_display(check['actual'])}\t{_display(check['expected'])}"
            )


def run_regression(cases: list[dict]) -> int:
    """呼叫真實 LLM 執行全部案例；單案例失敗不影響後續案例。"""
    from backend.llm.client import create_client

    client = create_client()
    sleep_seconds = float(os.getenv("LLM_SLEEP_SECONDS", DEFAULT_SLEEP_SECONDS))
    results: list[dict] = []
    for index, case in enumerate(cases):
        try:
            fields = summarize_event(case["articles"], client)
            enforce_official_guard(fields, case["articles"])
            checks = check_expectations(fields, case["articles"], case["expect"])
            results.append({
                "case_id": case["case_id"],
                "passed": all(check["passed"] for check in checks),
                "checks": checks,
            })
        except Exception as exc:
            results.append({"case_id": case["case_id"], "error": str(exc)})
        if sleep_seconds > 0 and index < len(cases) - 1:
            time.sleep(sleep_seconds)

    print_results(results)
    return 0 if all(result.get("passed", False) for result in results) else 1


def self_test() -> None:
    """以假 client 離線覆蓋四種 expectation 的 pass 與 fail 路徑。"""
    from dataclasses import dataclass

    @dataclass
    class _FakeResult:
        data: dict

    class _FakeClient:
        def __init__(self, data: dict):
            self.data = data

        def generate(self, system, user, schema):
            assert system and user and schema
            return _FakeResult(data=dict(self.data))

    article = {
        "source": "測試媒體",
        "source_type": "media",
        "published_at": "2026-07-27T09:00:00+00:00",
        "title": "測試標題",
        "content": "測試內容",
    }
    base = {
        "occurred_at_iso": None,
        "related_tickers": [],
        "status": "developing",
        "expected_direction": "利空",
    }

    def evaluate(data: dict, articles: list[dict], expect: dict) -> bool:
        fields = summarize_event(articles, _FakeClient(data))
        enforce_official_guard(fields, articles)
        return check_expectations(fields, articles, expect)[0]["passed"]

    assert evaluate(base, [article], {"occurred_at_within_published": True})
    assert not evaluate(
        {**base, "occurred_at_iso": "2026-08-28T14:00:00"},
        [article],
        {"occurred_at_within_published": True},
    )
    assert evaluate(base, [article], {"related_tickers_allowed": []})
    assert not evaluate(
        {**base, "related_tickers": ["2330"]},
        [article],
        {"related_tickers_allowed": []},
    )
    assert evaluate(
        {**base, "status": "official_confirmed"},
        [article],
        {"status_not": "official_confirmed"},
    )
    official_article = {**article, "source_type": "official"}
    assert not evaluate(
        {**base, "status": "official_confirmed"},
        [official_article],
        {"status_not": "official_confirmed"},
    )
    assert evaluate(base, [article], {"expected_direction": "利空"})
    assert not evaluate(base, [article], {"expected_direction": "利多"})
    print("regression.py 自我測試通過（四種 expectation，各覆蓋 pass / fail）")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="事件級 LLM 回歸跑器；只有明確指定 run 才會呼叫 LLM。"
    )
    parser.add_argument("action", nargs="?", choices=("run",), help="run：呼叫 LLM 跑回歸")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--self-test", action="store_true", help="離線驗證四種 expectation 檢查")
    mode.add_argument("--dry", action="store_true", help="只驗證 regression_cases.json 結構")
    args = parser.parse_args(argv)

    if args.action and (args.self_test or args.dry):
        parser.error("run 不可與 --self-test 或 --dry 同時使用")
    if args.self_test:
        self_test()
        return 0
    if not (args.dry or args.action == "run"):
        parser.print_help()
        return 0

    try:
        payload = load_cases()
    except (OSError, json.JSONDecodeError) as exc:
        print(f"案例檔讀取失敗: {exc}", file=sys.stderr)
        return 1
    cases, problems = validate_cases(payload)
    if problems:
        print("案例檔結構驗證失敗:", file=sys.stderr)
        for problem in problems:
            print(f"- {problem}", file=sys.stderr)
        return 1
    if args.dry:
        article_count = sum(len(case["articles"]) for case in cases)
        print(f"案例檔結構驗證通過: {len(cases)} 案例、{article_count} 篇文章")
        return 0
    return run_regression(cases)


if __name__ == "__main__":
    raise SystemExit(main())
