"""唯一營運入口：`python -m eventsignal <子命令>`。

這層只做兩件事——把子命令對應到既有模組的 `main()`，以及把剩下的參數原樣轉交。
它不解析子命令自己的參數，也不改變任何行為：`python -m eventsignal pipeline --stages llm`
與直接跑 `python -m eventsignal.pipeline --stages llm` 完全等價。

為什麼需要它：值得跑第二次的操作都要有名字，而名字要能在 `--help` 裡一次看完。
以前這些指令散落在 README、殼腳本與各人的 scrollback 裡（2026-08-11 的漏步驟事故
就是這樣來的）。

import 一律延遲到選定子命令之後——api 容器沒有 torch，embed 容器沒有 fastapi，
在這裡頂層 import 任何一個都會讓另一邊起不來。
"""
from __future__ import annotations

import importlib
import sys

# 子命令 → (模組, 函式, 一句話說明)
COMMANDS: dict[str, tuple[str, str, str]] = {
    "api":          ("eventsignal.main",                  "_serve",  "啟動只讀 API（uvicorn，預設 0.0.0.0:8000）"),
    "daily":        ("eventsignal.daily",                 "main",    "跑完整一輪每日管線（① 爬蟲 →⑥ 健康記分板）"),
    "pipeline":     ("eventsignal.pipeline",              "main",    "跑指定管線段：--stages embedding,clustering,llm,scoring"),
    "ingest":       ("eventsignal.ingestion",             "main",    "執行核准的 Scrapy 爬蟲並冪等寫入 articles"),
    "embed":        ("eventsignal.embedding.bge_m3",      "main",    "向量化 pending 文章（BGE-M3，1024 維）"),
    "finmind":      ("eventsignal.finmind.daily_batch",   "main",    "FinMind 籌碼每日批次"),
    "market-index": ("eventsignal.market_index.daily_batch", "main", "八國大盤指數日線批次"),
    "health":       ("eventsignal.pipeline_health",       "main",    "管線健康記分板（空轉會被抓出來）"),
    "serve-flows":  ("eventsignal.orchestration.serve",   "main",    "把三條 Prefect flow 掛上排程並常駐"),
}


def _usage() -> str:
    width = max(len(name) for name in COMMANDS)
    lines = [
        "用法：python -m eventsignal <子命令> [參數...]",
        "",
        "子命令：",
        *(f"  {name:<{width}}  {desc}" for name, (_, _, desc) in COMMANDS.items()),
        "",
        "各子命令自己的參數：python -m eventsignal <子命令> --help",
        "測試：pytest（後端）／pytest src/spider_forge/tests／pytest crawler/tests",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)

    if not args or args[0] in ("-h", "--help"):
        print(_usage())
        return 0

    name, rest = args[0], args[1:]
    if name not in COMMANDS:
        print(f"未知的子命令：{name}\n", file=sys.stderr)
        print(_usage(), file=sys.stderr)
        return 2

    module_path, func_name, _ = COMMANDS[name]
    module = importlib.import_module(module_path)
    # 被呼叫的 main() 自己用 argparse 讀 sys.argv；prog 名稱要讓 --help 印得出正確用法
    sys.argv = [f"python -m eventsignal {name}", *rest]
    result = getattr(module, func_name)()
    return 0 if result is None else int(result)


if __name__ == "__main__":
    raise SystemExit(main())
