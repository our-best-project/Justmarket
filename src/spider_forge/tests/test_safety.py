"""P0-3 / P0-5 回歸：沙盒不繼承 secrets、token usage 每站正確歸屬。

跑法（從 /）：
    python -m spider_forge.tests.test_safety
"""

from __future__ import annotations

import importlib
import os
import tempfile
from pathlib import Path

from spider_forge.clients import coder as coder_client
from spider_forge.shared import sandbox as runner_module
from spider_forge.shared.sandbox import sandbox_env

batch_module = importlib.import_module("spider_forge.runs.batch")

_SECRET_KEYS = ("DATABASE_URL", "DEEPSEEK_API", "KIMI_API", "OLLAMA_HOST",
                "LLM_API_KEY", "POSTGRES_PASSWORD", "FINMIND_TOKEN")


def t_sandbox_does_not_inherit_secrets():
    # 先確保這些 secret 真的在本進程環境裡（否則測試無效）
    os.environ.setdefault("DATABASE_URL", "postgresql://u:p@h/db")
    os.environ.setdefault("DEEPSEEK_API", "sk-deepseek-should-not-leak")
    os.environ.setdefault("KIMI_API", "sk-kimi-should-not-leak")

    env = sandbox_env(["news.cnyes.com", "api.cnyes.com"])
    leaked = [k for k in _SECRET_KEYS if k in env]
    dom_ok = env.get("SPIDERFORGE_ALLOWED_DOMAINS") == "news.cnyes.com,api.cnyes.com"
    utf8_ok = env.get("PYTHONUTF8") == "1"
    windows_home_ok = os.name != "nt" or bool(env.get("USERPROFILE"))
    ok = not leaked and dom_ok and utf8_ok and windows_home_ok
    return ok, (
        f"leaked={leaked} allowlist_ok={dom_ok} utf8_ok={utf8_ok} "
        f"windows_home_ok={windows_home_ok}"
    )


def t_usage_drain_reset_semantics():
    coder_client.drain_usage()  # 清乾淨
    coder_client._USAGE.append({"provider": "deepseek", "prompt_tokens": 10, "completion_tokens": 5})
    first = coder_client.drain_usage()
    second = coder_client.drain_usage()
    ok = len(first) == 1 and second == []
    return ok, f"first={len(first)} second={len(second)}"


def t_docker_candidate_uses_stdin_readonly_tmpfs_and_streams_output():
    captured = {}

    class Proc:
        returncode = 0
        stdout = "[]"
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env")
        captured["input"] = kwargs.get("input")
        return Proc()

    os.environ["DATABASE_URL"] = "postgresql://must-not-leak"
    os.environ["LLM_API_KEY"] = "must-not-leak"
    previous_docker_bin = os.environ.get("SPIDERFORGE_DOCKER_BIN")
    os.environ["SPIDERFORGE_DOCKER_BIN"] = "docker"
    original = runner_module.subprocess.run
    try:
        runner_module.subprocess.run = fake_run
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            candidate = root / "candidate.py"
            output = root / "out" / "items.json"
            candidate.write_text("print('fixture')", encoding="utf-8")
            result = runner_module._run_candidate_docker(
                str(candidate),
                out_json=str(output),
                allowed_domains=["example.com"],
                timeout_s=30,
                item_limit=5,
            )
            captured["output_text"] = output.read_text(encoding="utf-8")
    finally:
        runner_module.subprocess.run = original
        if previous_docker_bin is None:
            os.environ.pop("SPIDERFORGE_DOCKER_BIN", None)
        else:
            os.environ["SPIDERFORGE_DOCKER_BIN"] = previous_docker_bin

    command = "\n".join(captured.get("cmd") or [])
    ok = (
        result.ok
        and captured.get("output_text") == "[]"
        and "--read-only" in command
        and "no-new-privileges" in command
        and "/tmp/spider.py" in command
        and "-:json" in command
        and "type=bind" not in command
        and captured.get("input") == "print('fixture')"
        and "SPIDERFORGE_ALLOWED_DOMAINS=example.com" in command
        and "must-not-leak" not in command
        and not any(key in (captured.get("env") or {}) for key in _SECRET_KEYS)
    )
    return ok, f"exit={result.exit_code} stdin_tmpfs={ok}"


def t_usage_no_cross_site_bleed():
    # 模擬 run_site 每站開始 drain 的效果：A 站殘留不應算到 B 站
    coder_client.drain_usage()
    coder_client._USAGE.append({"provider": "deepseek", "prompt_tokens": 100, "completion_tokens": 50})
    # A 站殘留；B 站開始前 run_site 會 drain()，不能把殘留算給 B
    coder_client.drain_usage()  # 這是 run_site 入口 drain 的等價
    coder_client._USAGE.append({"provider": "deepseek", "prompt_tokens": 7, "completion_tokens": 3})
    b_usage = coder_client.drain_usage()
    b_tokens = sum(u["prompt_tokens"] + u["completion_tokens"] for u in b_usage)
    ok = b_tokens == 10  # 只有 B 站自己的 10，不含 A 站殘留的 150
    return ok, f"b_tokens={b_tokens} (期望 10)"


def t_error_run_records_its_own_usage():
    captured = []
    site = {"site_name": "故障站", "source_prefix": "_usageerror", "site_url": "https://example.com"}

    class FailingGraph:
        def stream(self, init, config, stream_mode):
            coder_client._USAGE.append(
                {"provider": "deepseek", "prompt_tokens": 11, "completion_tokens": 4}
            )
            raise RuntimeError("deliberate graph failure")
            yield  # pragma: no cover

    originals = {
        "load_sites": batch_module.load_sites,
        "build_pipeline": batch_module.build_pipeline,
        "append_run": batch_module.append_run,
        "summarize": batch_module.summarize,
    }
    try:
        batch_module.load_sites = lambda only=None: [site]
        batch_module.build_pipeline = lambda: FailingGraph()
        batch_module.append_run = captured.append
        batch_module.summarize = lambda: {}
        result = batch_module.run_batch()
    finally:
        for name, value in originals.items():
            setattr(batch_module, name, value)
        coder_client.drain_usage()

    rec = result[0]
    ok = (
        len(captured) == 1
        and rec["status"] == "error"
        and rec["llm_calls"] == 1
        and rec["coder_tokens"] == 15
    )
    return ok, f"status={rec['status']} calls={rec['llm_calls']} tokens={rec['coder_tokens']}"


def t_minimal_url_record_uses_graph_normalized_values():
    class State:
        values = {
            "site_name": "news.example.com",
            "source_prefix": "news_example_com",
            "site_url": "https://news.example.com/",
            "status": "success",
            "validation_result": {"item_count": 5, "valid_count": 5},
            "topic_result": {},
            "test_result": {},
        }

    class SuccessfulGraph:
        def stream(self, init, config, stream_mode):
            yield {"prepare_request": {"status": "request_ready"}}

        def get_state(self, config):
            return State()

    rec = batch_module.run_site(
        SuccessfulGraph(),
        {"site_url": "https://news.example.com/"},
        run_id="news_example_com-test",
    )
    ok = (
        rec["status"] == "success"
        and rec["site_name"] == "news.example.com"
        and rec["source_prefix"] == "news_example_com"
    )
    return ok, f"name={rec['site_name']} prefix={rec['source_prefix']}"


TESTS = [
    t_sandbox_does_not_inherit_secrets,
    t_docker_candidate_uses_stdin_readonly_tmpfs_and_streams_output,
    t_usage_drain_reset_semantics,
    t_usage_no_cross_site_bleed,
    t_error_run_records_its_own_usage,
    t_minimal_url_record_uses_graph_normalized_values,
]


def main() -> int:
    failed = 0
    for t in TESTS:
        try:
            ok, detail = t()
        except Exception as e:
            ok, detail = False, f"EXCEPTION {e}"
        print(f"[{'PASS' if ok else 'FAIL'}] {t.__name__}: {detail}")
        if not ok:
            failed += 1
    print(f"\n{len(TESTS) - failed}/{len(TESTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
