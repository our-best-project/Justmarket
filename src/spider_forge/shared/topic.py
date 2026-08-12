"""Spider Forge 主題判定共用服務。

預設由 Gemini 對「標題＋內文前 50 字」做批次 Structured Output 分類；
Python 仍負責完整性、交叉欄位、比例門檻與 fail-closed。舊 BGE artifact
保留為可明確指定的離線回退，不再是預設 production 路徑。
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from urllib.request import Request, urlopen

import numpy as np

from ..config import MODELS_DIR

_HERE = Path(__file__).parent
MODEL_DIR = MODELS_DIR
ARTIFACT_SCHEMA = "topic-gate-artifact/v1"
RESULT_SCHEMA = "topic-gate-result/v1"
TAXONOMY_VERSION = "iptc-subset+local-v1"
FEATURE_VERSION = "title+content-first-50/v1"
LABELS = ("finance", "public_policy")

DEFAULT_CONFIG = {
    "mode": "enforce",  # off | shadow | enforce
    "provider": "gemini",  # gemini | artifact
    "gemini_model": "gemini-3.5-flash-lite",
    "gemini_batch_size": 20,
    "gemini_timeout_s": 60.0,
    "artifact": "active.json",
    "advisor": "none",  # none | ollama
    "ollama_model": "qwen3:4b",
    "min_relevant_items": 5,
    "min_relevant_ratio": 0.6,
}


def normalize_config(raw: dict | None, *, min_valid_items: int = 5) -> dict:
    cfg = {
        **DEFAULT_CONFIG,
        "min_relevant_items": min_valid_items,
        **(raw or {}),
    }
    if not raw or "gemini_model" not in raw:
        cfg["gemini_model"] = os.getenv(
            "SPIDERFORGE_TOPIC_MODEL", DEFAULT_CONFIG["gemini_model"]
        )
    if cfg["mode"] not in {"off", "shadow", "enforce"}:
        raise ValueError("topic_gate.mode 僅允許 off、shadow、enforce")
    if cfg["provider"] not in {"gemini", "artifact"}:
        raise ValueError("topic_gate.provider 僅允許 gemini 或 artifact")
    if cfg["advisor"] not in {"none", "ollama"}:
        raise ValueError("topic_gate.advisor 僅允許 none 或 ollama")
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,100}", str(cfg["artifact"])):
        raise ValueError("topic_gate.artifact 只能是 topic_models 內的安全檔名")
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,100}", str(cfg["ollama_model"])):
        raise ValueError("topic_gate.ollama_model 格式不合法")
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,100}", str(cfg["gemini_model"])):
        raise ValueError("topic_gate.gemini_model 格式不合法")
    cfg["gemini_batch_size"] = int(cfg["gemini_batch_size"])
    if not 1 <= cfg["gemini_batch_size"] <= 20:
        raise ValueError("topic_gate.gemini_batch_size 必須介於 1 到 20")
    cfg["gemini_timeout_s"] = float(cfg["gemini_timeout_s"])
    if not 5.0 <= cfg["gemini_timeout_s"] <= 180.0:
        raise ValueError("topic_gate.gemini_timeout_s 必須介於 5 到 180 秒")
    cfg["min_relevant_items"] = max(
        1, int(cfg.get("min_relevant_items") or min_valid_items)
    )
    ratio = float(cfg.get("min_relevant_ratio", 0.6))
    if not 0.0 <= ratio <= 1.0:
        raise ValueError("topic_gate.min_relevant_ratio 必須介於 0 與 1")
    cfg["min_relevant_ratio"] = ratio
    return cfg


def feature_text(item: dict) -> str:
    title = " ".join(str(item.get("title") or "").split())
    content = " ".join(str(item.get("content") or "").split())
    return f"{title}\n{content[:50]}".strip()


def artifact_digest(artifact: dict) -> str:
    payload = copy.deepcopy(artifact)
    payload.pop("artifact_digest", None)
    raw = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def finalize_artifact(artifact: dict) -> dict:
    result = copy.deepcopy(artifact)
    result["artifact_digest"] = artifact_digest(result)
    return result


def _artifact_path(name: str) -> Path:
    base = MODEL_DIR.resolve()
    path = (base / name).resolve()
    if path.parent != base:
        raise ValueError("topic artifact 不得離開 topic_models")
    return path


def load_artifact(name: str, *, require_ready: bool) -> dict:
    path = _artifact_path(name)
    if not path.is_file():
        raise FileNotFoundError(f"topic artifact 不存在：{path}")
    artifact = json.loads(path.read_text(encoding="utf-8"))
    if artifact.get("schema_version") != ARTIFACT_SCHEMA:
        raise ValueError("topic artifact schema_version 不相容")
    if artifact.get("feature_version") != FEATURE_VERSION:
        raise ValueError("topic artifact feature_version 不相容")
    if artifact.get("labels") != list(LABELS):
        raise ValueError("topic artifact labels 不相容")
    if artifact.get("artifact_digest") != artifact_digest(artifact):
        raise ValueError("topic artifact digest 驗證失敗")
    dimension = int(artifact.get("embedding_dimension") or 0)
    if dimension <= 0:
        raise ValueError("topic artifact embedding_dimension 不合法")
    for label in LABELS:
        model = artifact.get("classifiers", {}).get(label) or {}
        if len(model.get("coef") or []) != dimension:
            raise ValueError(f"topic artifact {label} 維度不符")
        for key in (
            "intercept",
            "calibration_coef",
            "calibration_intercept",
            "accept_threshold",
            "reject_threshold",
        ):
            if not math.isfinite(float(model.get(key))):
                raise ValueError(f"topic artifact {label}.{key} 不合法")
        if float(model["reject_threshold"]) >= float(model["accept_threshold"]):
            raise ValueError(f"topic artifact {label} 門檻順序不合法")
    if require_ready and artifact.get("production_ready") is not True:
        raise ValueError("topic artifact 尚未通過保留集，不能 enforce")
    return artifact


@lru_cache(maxsize=2)
def _embedding_model(model_name: str):
    from sentence_transformers import SentenceTransformer

    device = os.environ.get("SPIDERFORGE_TOPIC_DEVICE", "cpu")
    return SentenceTransformer(model_name, device=device)


def _embed_texts(texts: list[str], artifact: dict) -> np.ndarray:
    model = _embedding_model(artifact["embedding_model"])
    vectors = model.encode(
        texts,
        batch_size=16,
        normalize_embeddings=True,
        show_progress_bar=False,
        convert_to_numpy=True,
    )
    return np.asarray(vectors, dtype=np.float64)


def _sigmoid(value: np.ndarray) -> np.ndarray:
    clipped = np.clip(value, -35.0, 35.0)
    return 1.0 / (1.0 + np.exp(-clipped))


def classify_with_artifact(
    items: list[dict],
    artifact: dict,
    *,
    embed_fn: Callable[[list[str], dict], np.ndarray] | None = None,
) -> dict:
    """以純 JSON 線性 artifact 分類；embed_fn 注入點供離線測試。"""
    if not items:
        return {"decisions": [], "relevant_count": 0, "review_count": 0}
    vectors = (embed_fn or _embed_texts)(
        [feature_text(item) for item in items], artifact
    )
    expected = (len(items), int(artifact["embedding_dimension"]))
    if tuple(vectors.shape) != expected:
        raise ValueError(f"topic embedding shape={vectors.shape}，期望 {expected}")

    scores: dict[str, np.ndarray] = {}
    for label in LABELS:
        spec = artifact["classifiers"][label]
        raw = vectors @ np.asarray(spec["coef"], dtype=np.float64)
        raw += float(spec["intercept"])
        calibrated = (
            float(spec["calibration_coef"]) * raw
            + float(spec["calibration_intercept"])
        )
        scores[label] = _sigmoid(calibrated)

    decisions = []
    for index, item in enumerate(items):
        item_scores = {label: round(float(scores[label][index]), 6) for label in LABELS}
        accepted = [
            label
            for label in LABELS
            if item_scores[label]
            >= float(artifact["classifiers"][label]["accept_threshold"])
        ]
        all_rejected = all(
            item_scores[label]
            <= float(artifact["classifiers"][label]["reject_threshold"])
            for label in LABELS
        )
        decision = "accept" if accepted else ("reject" if all_rejected else "review")
        decisions.append(
            {
                "index": index,
                "url": item.get("url"),
                "title": item.get("title"),
                "decision": decision,
                "labels": accepted,
                "scores": item_scores,
            }
        )
    return {
        "decisions": decisions,
        "relevant_count": sum(x["decision"] == "accept" for x in decisions),
        "review_count": sum(x["decision"] == "review" for x in decisions),
        "reject_count": sum(x["decision"] == "reject" for x in decisions),
    }


def _ollama_digest(model: str, timeout_s: float) -> str | None:
    with urlopen("http://127.0.0.1:11434/api/tags", timeout=timeout_s) as response:
        data = json.loads(response.read().decode("utf-8"))
    for entry in data.get("models", []):
        if entry.get("name") == model or entry.get("model") == model:
            return entry.get("digest")
    return None


def _validate_advisory_rows(rows: list[dict], expected: set[int]) -> list[dict]:
    observed = {row.get("index") for row in rows}
    if observed != expected or len(rows) != len(expected):
        raise ValueError(
            f"batch index 不完整：expected={sorted(expected)} "
            f"observed={sorted(x for x in observed if x is not None)}"
        )
    cleaned = []
    for row in rows:
        decision = row.get("decision")
        labels = row.get("labels")
        if decision not in {"accept", "reject", "review"}:
            raise ValueError(f"index={row.get('index')} decision 不合法")
        if (
            not isinstance(labels, list)
            or any(label not in LABELS for label in labels)
            or len(labels) != len(set(labels))
        ):
            raise ValueError(f"index={row.get('index')} labels 不合法")
        if decision == "accept" and not labels:
            raise ValueError(f"index={row.get('index')} accept 卻沒有 label")
        if decision == "reject" and labels:
            raise ValueError(f"index={row.get('index')} reject 卻仍有 label")
        cleaned.append(
            {
                **row,
                "evidence": str(row.get("evidence") or "")[:120],
            }
        )
    return cleaned


def classify_with_gemini(
    items: list[dict],
    *,
    model: str = "gemini-3.5-flash-lite",
    batch_size: int = 20,
    timeout_s: float = 60.0,
    classify_batch_fn: Callable | None = None,
) -> dict:
    """Batch Gemini calls; Python validates every row before it can gate."""
    from ..clients.topic import classify_batch

    call = classify_batch_fn or classify_batch
    decisions: list[dict] = []
    usage_totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    interaction_ids: list[str] = []
    for start in range(0, len(items), batch_size):
        batch_items = items[start : start + batch_size]
        rows = [
            {
                "index": start + offset,
                "title": item.get("title"),
                "content_lead": str(item.get("content") or "")[:50],
            }
            for offset, item in enumerate(batch_items)
        ]
        response = call(rows, model=model, timeout_s=timeout_s)
        expected = {row["index"] for row in rows}
        cleaned = _validate_advisory_rows(response.get("items") or [], expected)
        for row in cleaned:
            item = items[row["index"]]
            decisions.append(
                {
                    **row,
                    "url": item.get("url"),
                    "title": item.get("title"),
                }
            )
        usage = response.get("usage") or {}
        for key in usage_totals:
            usage_totals[key] += int(usage.get(key) or 0)
        if response.get("interaction_id"):
            interaction_ids.append(str(response["interaction_id"]))
    decisions.sort(key=lambda row: row["index"])
    return {
        "decisions": decisions,
        "relevant_count": sum(x["decision"] == "accept" for x in decisions),
        "review_count": sum(x["decision"] == "review" for x in decisions),
        "reject_count": sum(x["decision"] == "reject" for x in decisions),
        "api_calls": len(range(0, len(items), batch_size)),
        "usage": usage_totals,
        "interaction_ids": interaction_ids,
    }


def ollama_advisory(
    items: list[dict], *, model: str = "qwen3:4b", timeout_s: float = 45.0
) -> dict:
    """地端 LLM 只提供 shadow 建議；任何錯誤都回 advisory_error。"""
    rows = [
        {
            "index": i,
            "title": item.get("title"),
            "content_lead": str(item.get("content") or "")[:500],
        }
        for i, item in enumerate(items)
    ]
    schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer"},
                        "decision": {
                            "type": "string",
                            "enum": ["accept", "reject", "review"],
                        },
                        "labels": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "enum": ["finance", "public_policy"],
                            },
                        },
                        "evidence": {"type": "string", "maxLength": 80},
                    },
                    "required": ["index", "decision", "labels", "evidence"],
                },
            }
        },
        "required": ["items"],
    }
    results = []
    errors = []
    for start in range(0, len(rows), 5):
        batch = rows[start : start + 5]
        prompt = (
            "你是新聞主題標註員，不是 promotion gate。finance 指市場、公司財務、"
            "產業經濟、投資；public_policy 指政府正式政策、法規、制裁、外交協議。"
            "單純政治人物動態、健康、娛樂、生活即使提到『公司/市場』也不算。"
            "混合主題可給兩個 labels；證據不足回 review。evidence 最多 30 個中文字。"
            "accept 必須至少有一個 label；reject 的 labels 必須是空陣列；"
            "review 可有候選 labels。只輸出符合 schema 的 JSON，且每個 index 恰好一次。\n"
            + json.dumps(batch, ensure_ascii=False)
        )
        body = json.dumps(
            {
                "model": model,
                "stream": False,
                "think": False,
                "format": schema,
                "messages": [{"role": "user", "content": prompt}],
                "options": {"temperature": 0, "seed": 0, "num_predict": 1200},
            },
            ensure_ascii=False,
        ).encode("utf-8")
        try:
            request = Request(
                "http://127.0.0.1:11434/api/chat",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request, timeout=timeout_s) as response:
                payload = json.loads(response.read().decode("utf-8"))
            parsed = json.loads(payload["message"]["content"]).get("items", [])
            expected = {row["index"] for row in batch}
            results.extend(_validate_advisory_rows(parsed, expected))
        except Exception as exc:
            errors.append({"start": start, "error": str(exc)[:300]})
    result = {
        "model": model,
        "model_digest": None,
        "advisory_only": True,
        "items": sorted(results, key=lambda row: row["index"]),
    }
    try:
        result["model_digest"] = _ollama_digest(model, timeout_s=5.0)
    except Exception as exc:
        errors.append({"start": "digest", "error": str(exc)[:300]})
    if errors:
        result["advisory_errors"] = errors
    return result


def evaluate_topic_gate(state: dict) -> dict:
    """Graph node：結構驗證後執行；enforce 的模型/API 錯誤一律 fail-closed。"""
    cfg = state.get("topic_gate") or normalize_config(None)
    structural = copy.deepcopy(state.get("validation_result") or {})
    base = {
        "schema_version": RESULT_SCHEMA,
        "taxonomy_version": TAXONOMY_VERSION,
        "mode": cfg["mode"],
        "provider": cfg["provider"],
        "status": "disabled",
        "gate_pass": None,
        "advisory": None,
    }
    if cfg["mode"] == "off":
        return {"topic_result": base, "validation_result": structural}
    if structural.get("pass") is not True:
        base["status"] = "skipped_structural_failure"
        return {"topic_result": base, "validation_result": structural}

    try:
        output_path = Path(state.get("test_result", {}).get("output_path", ""))
        items = json.loads(output_path.read_text(encoding="utf-8"))
        valid_indices = structural.get("valid_item_indices", list(range(len(items))))
        valid_items = [items[i] for i in valid_indices]
    except Exception as exc:
        base.update(status="input_error", error=str(exc)[:500], gate_pass=False)
        valid_items = []

    if not valid_items:
        if base["status"] != "input_error":
            base.update(status="no_valid_items", gate_pass=False)
        structural.setdefault("flags", {})["topic_ok"] = cfg["mode"] != "enforce"
        if cfg["mode"] == "enforce":
            structural["pass"] = False
            structural.setdefault("issues", []).append(
                f"topic_gate_fail:{base['status']}"
            )
        return {"topic_result": base, "validation_result": structural}

    try:
        if cfg["provider"] == "gemini":
            scored = classify_with_gemini(
                valid_items,
                model=cfg["gemini_model"],
                batch_size=cfg["gemini_batch_size"],
                timeout_s=cfg["gemini_timeout_s"],
            )
            model = cfg["gemini_model"]
            provenance = {
                "api_calls": scored["api_calls"],
                "usage": scored["usage"],
                "interaction_ids": scored["interaction_ids"],
            }
        else:
            artifact = load_artifact(
                cfg["artifact"], require_ready=cfg["mode"] == "enforce"
            )
            scored = classify_with_artifact(valid_items, artifact)
            model = artifact["embedding_model"]
            provenance = {"artifact_digest": artifact["artifact_digest"]}
        ratio = scored["relevant_count"] / len(valid_items) if valid_items else 0.0
        gate_pass = (
            scored["relevant_count"] >= cfg["min_relevant_items"]
            and ratio >= cfg["min_relevant_ratio"]
        )
        base.update(
            status="scored",
            gate_pass=gate_pass,
            model=model,
            relevant_count=scored["relevant_count"],
            review_count=scored["review_count"],
            reject_count=scored["reject_count"],
            relevant_ratio=round(ratio, 3),
            decisions=scored["decisions"],
            **provenance,
        )
    except Exception as exc:
        base.update(
            status=f"{cfg['provider']}_unavailable",
            gate_pass=False,
            error=str(exc)[:500],
            relevant_count=0,
            review_count=len(valid_items),
            reject_count=0,
            relevant_ratio=0.0,
        )

    if cfg.get("advisor") == "ollama" and valid_items:
        base["advisory"] = ollama_advisory(
            valid_items, model=cfg["ollama_model"]
        )

    structural.setdefault("flags", {})["topic_ok"] = (
        base["gate_pass"] if cfg["mode"] == "enforce" else True
    )
    if cfg["mode"] == "enforce" and base["gate_pass"] is not True:
        structural["pass"] = False
        structural.setdefault("issues", []).append(
            f"topic_gate_fail:{base['status']}"
        )
    return {"topic_result": base, "validation_result": structural}
