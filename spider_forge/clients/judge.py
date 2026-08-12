"""本地判斷模型 client（Ollama）——judge 節點共用（docs/spec/08 §2）。

用 Ollama 的 JSON-schema `format` 做 constrained decoding，機制性保證合法 JSON
（比 prompt 硬要「請回 JSON」可靠）。實測 qwen2.5:7b-instruct 於 RTX 4060 8GB 可用。

judge 節點（strategy_decision / validate_output / diagnose_failure）都走這裡；
強模型「產 code / 修 code」走另一個 coder_client（複用 llm_MMJSUN/client.py，M2 另接）。

環境變數：
  OLLAMA_HOST              預設 http://localhost:11434
  SPIDERFORGE_JUDGE_MODEL  預設 qwen2.5:7b-instruct
"""

from __future__ import annotations

import json
import os
from typing import Any

import requests

OLLAMA_URL = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
DEFAULT_MODEL = os.environ.get("SPIDERFORGE_JUDGE_MODEL", "qwen2.5:7b-instruct")


class JudgeError(RuntimeError):
    """本地模型連線失敗 / 回傳非合法 JSON。"""


def judge(
    *,
    system: str,
    user: str,
    schema: dict[str, Any],
    model: str | None = None,
    num_ctx: int = 8192,
    timeout_s: int = 120,
) -> dict[str, Any]:
    """呼叫本地模型，回傳符合 schema 的 dict。

    temperature=0 求判斷可重現。連不上 Ollama 或回傳非法 JSON → 拋 JudgeError
    （fail-fast，不腦補一個看似合理的結果，符合 research-harness 原則）。
    """
    payload = {
        "model": model or DEFAULT_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "format": schema,  # ← JSON schema constrained decoding
        "stream": False,
        "options": {"num_ctx": num_ctx, "temperature": 0},
    }
    try:
        resp = requests.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=timeout_s)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise JudgeError(f"Ollama 連線失敗（{OLLAMA_URL}）：{e}") from e

    content = resp.json().get("message", {}).get("content", "")
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        raise JudgeError(f"模型回傳非合法 JSON：{content!r}") from e
