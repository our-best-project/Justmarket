"""Spider Forge Docker／本機統一 CLI。"""

from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from .config import (
    REQUESTS_DIR,
    ensure_runtime_layout,
    location_map,
    run_dir,
)
from .pipeline import build_pipeline
from .runs.batch import run_batch, run_one
from .runs.ledger import summarize


class _Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, value: str) -> int:
        for stream in self.streams:
            stream.write(value)
            stream.flush()
        return len(value)

    def flush(self) -> None:
        for stream in self.streams:
            stream.flush()


def _urls_from_file(path: Path) -> list[str]:
    if not path.is_file():
        raise FileNotFoundError(
            f"找不到 URL 清單：{path}；請每行放一個 http/https URL"
        )
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _validate_url(value: str) -> str:
    url = value.strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"不是有效的 http/https URL：{value!r}")
    return url


def _derived_prefix(url: str) -> str:
    host = urlparse(url).hostname or "generated_source"
    value = re.sub(r"[^a-z0-9]+", "_", host.lower()).strip("_")
    return (value or "generated_source")[:40]


def _run_id(prefix: str) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}-{timestamp}-{uuid.uuid4().hex[:8]}"


def _run(args: argparse.Namespace) -> int:
    ensure_runtime_layout()
    urls = list(args.url or [])
    if args.file:
        urls.extend(_urls_from_file(args.file))
    urls = list(dict.fromkeys(_validate_url(value) for value in urls))
    if not urls:
        raise ValueError(
            "沒有可執行 URL；請使用 --url，或寫入 "
            f"{REQUESTS_DIR / 'urls.txt'}"
        )
    if (args.prefix or args.name) and len(urls) != 1:
        raise ValueError("--prefix/--name 只適用單一 URL")

    graph = build_pipeline()
    records: list[dict] = []
    for url in urls:
        prefix = args.prefix or _derived_prefix(url)
        run_id = _run_id(prefix)
        directory = run_dir(run_id)
        log_path = directory / "pipeline.log"
        site = {"site_url": url}
        if args.prefix:
            site["source_prefix"] = args.prefix
        if args.name:
            site["site_name"] = args.name

        with log_path.open("a", encoding="utf-8", buffering=1) as log:
            stdout = _Tee(sys.stdout, log)
            stderr = _Tee(sys.stderr, log)
            with redirect_stdout(stdout), redirect_stderr(stderr):
                print(f"===== Spider Forge {run_id} =====")
                print(f"URL: {url}")
                print(f"Log: {log_path}")
                record = run_one(
                    graph,
                    site,
                    max_retries=args.max_retries,
                    run_id=run_id,
                )
                print(json.dumps(record, ensure_ascii=False, indent=2))
        records.append(record)

    print(json.dumps({"runs": records, "paths": location_map()}, ensure_ascii=False, indent=2))
    if any(record.get("status") == "error" for record in records):
        return 1
    if any(record.get("status") != "success" for record in records):
        return 2
    return 0


def _status(_: argparse.Namespace) -> int:
    ensure_runtime_layout()
    print(
        json.dumps(
            {"summary": summarize(), "paths": location_map()},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _batch(args: argparse.Namespace) -> int:
    ensure_runtime_layout()
    records = run_batch(
        list(args.prefix) or None,
        max_retries=args.max_retries,
    )
    if any(record.get("status") == "error" for record in records):
        return 1
    if any(record.get("status") != "success" for record in records):
        return 2
    return 0


def _train_topic(args: argparse.Namespace) -> int:
    from .tools.topic_training import train_topic_artifact

    summary = train_topic_artifact(
        args.gold_jsonl,
        output=args.output,
        model=args.model,
        min_precision=args.min_precision,
        min_npv=args.min_npv,
        min_calibration_count=args.min_calibration_count,
        min_test_accept_count=args.min_test_accept_count,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["production_ready"] else 2


def _paths(_: argparse.Namespace) -> int:
    ensure_runtime_layout()
    print(json.dumps(location_map(), ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="spider-forge",
        description=(
            "輸入網站 URL，執行偵查 → 證據 → 生成 → 預檢 → "
            "保存樣本重播 → 隔離執行 → 品質閘門 → 升版。"
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="執行一個或多個網站")
    run.add_argument("--url", action="append", help="目標 URL；可重複")
    run.add_argument("--file", type=Path, help="每行一個 URL 的 UTF-8 文字檔")
    run.add_argument("--prefix", help="單一 URL 的 source_prefix")
    run.add_argument("--name", help="單一 URL 的顯示名稱")
    run.add_argument("--max-retries", type=int, choices=(0, 1, 2), default=2)
    run.set_defaults(handler=_run)

    batch = subparsers.add_parser(
        "batch",
        help="執行 site_queue.yaml；可指定一個或多個 source_prefix",
    )
    batch.add_argument(
        "prefix",
        nargs="*",
        help="site_queue.yaml 的 source_prefix；省略時執行全部",
    )
    batch.add_argument(
        "--max-retries",
        type=int,
        choices=(0, 1, 2),
        default=2,
        help="每站最多模型修復次數",
    )
    batch.set_defaults(handler=_batch)

    status = subparsers.add_parser("status", help="顯示歷史執行摘要與資料位置")
    status.set_defaults(handler=_status)

    paths = subparsers.add_parser("paths", help="只顯示輸入、產物與紀錄位置")
    paths.set_defaults(handler=_paths)

    train = subparsers.add_parser(
        "train-topic",
        help="以 JSONL 金標訓練／校準選用的離線主題模型",
    )
    train.add_argument("gold_jsonl", type=Path)
    train.add_argument("--output", default="candidate.json")
    train.add_argument("--model", default="BAAI/bge-m3")
    train.add_argument("--min-precision", type=float, default=0.95)
    train.add_argument("--min-npv", type=float, default=0.95)
    train.add_argument("--min-calibration-count", type=int, default=10)
    train.add_argument("--min-test-accept-count", type=int, default=10)
    train.set_defaults(handler=_train_topic)
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except (FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
