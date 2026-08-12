"""以正式 fixture gate 重播人工候選，不另外維護第二套驗證邏輯。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ...shared.fixture import fixture_test
from ...shared.repair import diagnose_failure


def validate(state_path: Path, candidate_path: Path) -> dict:
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["spider_code"] = candidate_path.read_text(encoding="utf-8")
    return fixture_test(state)["fixture_result"]


def _result_state(state: dict, result: dict) -> dict:
    merged = {
        **state,
        "generation_preflight": state.get("generation_preflight")
        or {"passed": True, "errors": []},
        "fixture_result": result,
        "status": "testing" if result.get("passed") else "validating",
    }
    if result.get("passed"):
        return merged
    return {**merged, **diagnose_failure(merged)}


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="以正式離線樣本閘門驗證 generate 候選"
    )
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--output-state", type=Path)
    args = parser.parse_args(argv)

    state = json.loads(args.state.read_text(encoding="utf-8"))
    state["spider_code"] = args.candidate.read_text(encoding="utf-8")
    result = fixture_test(state)["fixture_result"]
    if args.output_state:
        args.output_state.parent.mkdir(parents=True, exist_ok=True)
        args.output_state.write_text(
            json.dumps(
                _result_state(state, result),
                ensure_ascii=False,
                indent=2,
                default=str,
            )
            + "\n",
            encoding="utf-8",
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
