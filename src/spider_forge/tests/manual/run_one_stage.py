"""一次只執行一個 Spider Forge 關卡，供人工活站審查。

本工具刻意沒有「全部執行」模式。輸入是前一關的完整狀態，stdout 只顯示
本關更新，輸出檔保存合併後狀態，讓審查者確認後才決定是否執行下一關。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path

from ...clients.coder import drain_usage
from ...stages.evidence import collect_evidence
from ...stages.fixture import fixture_test
from ...stages.generate import (
    generate_spider,
    preflight_generated_code,
    strategy_decision,
)
from ...stages.probe import prepare_request, recon
from ...stages.repair import repair_code, repair_code_kimi
from ...stages.triage import feasibility_triage

Stage = Callable[[dict], dict]
STAGES: dict[str, Stage] = {
    "prepare": prepare_request,
    "recon": recon,
    "triage": feasibility_triage,
    "strategy": strategy_decision,
    "evidence": collect_evidence,
    "generate": generate_spider,
    "preflight": preflight_generated_code,
    "fixture": fixture_test,
    "repair": repair_code,
    "repair_kimi": repair_code_kimi,
}


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="一次執行一個 Spider Forge 關卡"
    )
    parser.add_argument("stage", choices=tuple(STAGES))
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--candidate",
        type=Path,
        help="preflight／fixture 時以此 Python 檔取代狀態中的 spider_code",
    )
    args = parser.parse_args(argv)

    state = json.loads(args.input.read_text(encoding="utf-8"))
    if args.candidate:
        if args.stage not in {"preflight", "fixture"}:
            parser.error("--candidate 只可搭配 preflight 或 fixture")
        state["spider_code"] = args.candidate.read_text(encoding="utf-8")
    update = STAGES[args.stage](state)
    if args.stage in {"generate", "repair", "repair_kimi"}:
        update["generation_usage"] = drain_usage()
    merged = {**state, **update}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.stage == "preflight" and update["generation_preflight"]["passed"]:
        code_path = args.output.with_suffix(".py")
        code_path.write_text(update["spider_code"], encoding="utf-8")
        update["generation_preflight"]["candidate_path"] = str(code_path)
        merged["generation_preflight"] = update["generation_preflight"]
    args.output.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(update, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
