"""標準套件 CLI 的離線契約測試。"""

from __future__ import annotations

from spider_forge import cli


def t_cli_exposes_all_supported_workflows():
    parser = cli.build_parser()
    parsed = {
        "run": parser.parse_args(
            ["run", "--url", "https://example.com/news"]
        ).command,
        "batch": parser.parse_args(["batch", "cnyes"]).command,
        "status": parser.parse_args(["status"]).command,
        "paths": parser.parse_args(["paths"]).command,
        "train": parser.parse_args(
            ["train-topic", "gold.jsonl"]
        ).command,
    }
    expected = {
        "run": "run",
        "batch": "batch",
        "status": "status",
        "paths": "paths",
        "train": "train-topic",
    }
    return parsed == expected, f"commands={parsed}"


def t_batch_cli_forwards_prefix_and_retry_budget():
    captured = {}
    original = cli.run_batch

    def fake_run_batch(prefixes, *, max_retries):
        captured["prefixes"] = prefixes
        captured["max_retries"] = max_retries
        return [{"status": "success"}]

    cli.run_batch = fake_run_batch
    try:
        exit_code = cli.main(
            ["batch", "cnyes", "cna", "--max-retries", "1"]
        )
    finally:
        cli.run_batch = original
    ok = (
        exit_code == 0
        and captured
        == {"prefixes": ["cnyes", "cna"], "max_retries": 1}
    )
    return ok, f"exit={exit_code} captured={captured}"


TESTS = [
    t_cli_exposes_all_supported_workflows,
    t_batch_cli_forwards_prefix_and_retry_budget,
]
