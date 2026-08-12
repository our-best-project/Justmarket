"""Topic gate 的離線安全契約測試；不下載模型、不呼叫 Ollama。"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import numpy as np

from spider_forge.clients import topic as gemini_topic_client
from spider_forge.shared import topic as topic_gate


def _artifact(*, ready=True):
    return topic_gate.finalize_artifact(
        {
            "schema_version": topic_gate.ARTIFACT_SCHEMA,
            "taxonomy_version": topic_gate.TAXONOMY_VERSION,
            "feature_version": topic_gate.FEATURE_VERSION,
            "embedding_model": "_fake_bge",
            "embedding_dimension": 2,
            "labels": list(topic_gate.LABELS),
            "classifiers": {
                "finance": {
                    "coef": [5.0, 0.0],
                    "intercept": 0.0,
                    "calibration_coef": 1.0,
                    "calibration_intercept": 0.0,
                    "accept_threshold": 0.8,
                    "reject_threshold": 0.2,
                },
                "public_policy": {
                    "coef": [0.0, 5.0],
                    "intercept": 0.0,
                    "calibration_coef": 1.0,
                    "calibration_intercept": 0.0,
                    "accept_threshold": 0.8,
                    "reject_threshold": 0.2,
                },
            },
            "production_ready": ready,
        }
    )


def _items():
    return [
        {"title": "財經", "content": "市場", "url": "https://x.test/1"},
        {"title": "政策", "content": "法規", "url": "https://x.test/2"},
        {"title": "無關", "content": "娛樂", "url": "https://x.test/3"},
    ]


def _fake_embed(texts, artifact):
    assert len(texts) == 3 and artifact["embedding_dimension"] == 2
    return np.asarray([[1.0, 0.0], [0.0, 1.0], [-1.0, -1.0]])


def t_artifact_classification_is_three_state():
    result = topic_gate.classify_with_artifact(
        _items(), _artifact(), embed_fn=_fake_embed
    )
    states = [row["decision"] for row in result["decisions"]]
    ok = states == ["accept", "accept", "reject"] and result["relevant_count"] == 2
    return ok, f"states={states} relevant={result['relevant_count']}"


def t_artifact_digest_tamper_is_detected():
    artifact = _artifact()
    artifact["classifiers"]["finance"]["coef"][0] = 99.0
    path = topic_gate.MODEL_DIR / "_test_tampered.json"
    topic_gate.MODEL_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(artifact), encoding="utf-8")
    try:
        try:
            topic_gate.load_artifact(path.name, require_ready=True)
            raised = False
        except ValueError:
            raised = True
    finally:
        path.unlink(missing_ok=True)
    return raised, f"raised={raised}"


def t_llm_advisory_cross_field_conflict_is_rejected():
    try:
        topic_gate._validate_advisory_rows(
            [
                {
                    "index": 0,
                    "decision": "reject",
                    "labels": ["finance"],
                    "evidence": "自相矛盾",
                }
            ],
            {0},
        )
        raised = False
    except ValueError:
        raised = True
    return raised, f"raised={raised}"


def t_shadow_without_artifact_does_not_fake_failure_or_success():
    output = Path(tempfile.gettempdir()) / "_topic_shadow_items.json"
    output.write_text(json.dumps(_items()), encoding="utf-8")
    try:
        result = topic_gate.evaluate_topic_gate(
            {
                "topic_gate": topic_gate.normalize_config(
                    {
                        "mode": "shadow",
                        "provider": "artifact",
                        "artifact": "_missing.json",
                    }
                ),
                "test_result": {"output_path": str(output)},
                "validation_result": {
                    "pass": True,
                    "flags": {},
                    "valid_item_indices": [0, 1, 2],
                },
            }
        )
    finally:
        output.unlink(missing_ok=True)
    ok = (
        result["validation_result"]["pass"] is True
        and result["topic_result"]["status"] == "artifact_unavailable"
        and result["topic_result"]["gate_pass"] is False
    )
    return ok, (
        f"pass={result['validation_result']['pass']} "
        f"status={result['topic_result']['status']}"
    )


def t_enforce_without_ready_artifact_fails_closed():
    output = Path(tempfile.gettempdir()) / "_topic_enforce_items.json"
    output.write_text(json.dumps(_items()), encoding="utf-8")
    try:
        result = topic_gate.evaluate_topic_gate(
            {
                "topic_gate": topic_gate.normalize_config(
                    {
                        "mode": "enforce",
                        "provider": "artifact",
                        "artifact": "_missing.json",
                    }
                ),
                "test_result": {"output_path": str(output)},
                "validation_result": {
                    "pass": True,
                    "flags": {},
                    "issues": [],
                    "valid_item_indices": [0, 1, 2],
                },
            }
        )
    finally:
        output.unlink(missing_ok=True)
    ok = (
        result["validation_result"]["pass"] is False
        and result["validation_result"]["flags"]["topic_ok"] is False
    )
    return ok, f"pass={result['validation_result']['pass']}"


def t_enforce_empty_valid_set_fails_closed_before_model_load():
    result = topic_gate.evaluate_topic_gate(
        {
            "topic_gate": topic_gate.normalize_config({"mode": "enforce"}),
            "test_result": {"output_path": "_missing_output.json"},
            "validation_result": {
                "pass": True,
                "flags": {},
                "issues": [],
                "valid_item_indices": [],
            },
        }
    )
    ok = (
        result["validation_result"]["pass"] is False
        and result["topic_result"]["status"] == "input_error"
    )
    return ok, (
        f"pass={result['validation_result']['pass']} "
        f"status={result['topic_result']['status']}"
    )


def t_enforce_ready_artifact_can_pass():
    artifact_path = topic_gate.MODEL_DIR / "_test_ready.json"
    output = Path(tempfile.gettempdir()) / "_topic_ready_items.json"
    topic_gate.MODEL_DIR.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(json.dumps(_artifact()), encoding="utf-8")
    output.write_text(json.dumps(_items()), encoding="utf-8")
    original = topic_gate._embed_texts
    topic_gate._embed_texts = _fake_embed
    try:
        result = topic_gate.evaluate_topic_gate(
            {
                "topic_gate": topic_gate.normalize_config(
                    {
                        "mode": "enforce",
                        "provider": "artifact",
                        "artifact": artifact_path.name,
                        "min_relevant_items": 2,
                        "min_relevant_ratio": 0.6,
                    }
                ),
                "test_result": {"output_path": str(output)},
                "validation_result": {
                    "pass": True,
                    "flags": {},
                    "issues": [],
                    "valid_item_indices": [0, 1, 2],
                },
            }
        )
    finally:
        topic_gate._embed_texts = original
        artifact_path.unlink(missing_ok=True)
        output.unlink(missing_ok=True)
    ok = (
        result["validation_result"]["pass"] is True
        and result["validation_result"]["flags"]["topic_ok"] is True
        and result["topic_result"]["relevant_count"] == 2
    )
    return ok, (
        f"pass={result['validation_result']['pass']} "
        f"relevant={result['topic_result']['relevant_count']}"
    )


def t_gemini_batches_twenty_and_sends_only_fifty_chars():
    seen = []
    items = [
        {
            "title": f"新聞 {index}",
            "content": "甲" * 80,
            "url": f"https://x.test/{index}",
        }
        for index in range(23)
    ]

    def fake_batch(rows, *, model, timeout_s):
        seen.append((rows, model, timeout_s))
        return {
            "items": [
                {
                    "index": row["index"],
                    "decision": "accept",
                    "labels": ["finance"],
                    "evidence": "財經",
                }
                for row in rows
            ],
            "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            "interaction_id": f"test-{len(seen)}",
        }

    result = topic_gate.classify_with_gemini(
        items,
        batch_size=20,
        classify_batch_fn=fake_batch,
    )
    batch_sizes = [len(call[0]) for call in seen]
    leads_ok = all(
        len(row["content_lead"]) == 50 for call in seen for row in call[0]
    )
    ok = (
        batch_sizes == [20, 3]
        and leads_ok
        and result["relevant_count"] == 23
        and result["api_calls"] == 2
        and result["usage"]["total_tokens"] == 30
    )
    return ok, f"batches={batch_sizes} leads_ok={leads_ok}"


def t_gemini_enforce_can_pass_and_failure_fails_closed():
    output = Path(tempfile.gettempdir()) / "_topic_gemini_items.json"
    output.write_text(json.dumps(_items()), encoding="utf-8")
    original = topic_gate.classify_with_gemini
    state = {
        "topic_gate": topic_gate.normalize_config(
            {
                "mode": "enforce",
                "provider": "gemini",
                "min_relevant_items": 2,
                "min_relevant_ratio": 0.6,
            }
        ),
        "test_result": {"output_path": str(output)},
        "validation_result": {
            "pass": True,
            "flags": {},
            "issues": [],
            "valid_item_indices": [0, 1, 2],
        },
    }
    try:
        topic_gate.classify_with_gemini = lambda *args, **kwargs: {
            "decisions": [
                {"index": 0, "decision": "accept"},
                {"index": 1, "decision": "accept"},
                {"index": 2, "decision": "reject"},
            ],
            "relevant_count": 2,
            "review_count": 0,
            "reject_count": 1,
            "api_calls": 1,
            "usage": {},
            "interaction_ids": [],
        }
        passed = topic_gate.evaluate_topic_gate(state)
        topic_gate.classify_with_gemini = lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("API down")
        )
        failed = topic_gate.evaluate_topic_gate(state)
    finally:
        topic_gate.classify_with_gemini = original
        output.unlink(missing_ok=True)
    ok = (
        passed["validation_result"]["pass"] is True
        and failed["validation_result"]["pass"] is False
        and failed["topic_result"]["status"] == "gemini_unavailable"
    )
    return ok, (
        f"pass={passed['validation_result']['pass']} "
        f"fail_closed={not failed['validation_result']['pass']}"
    )


def t_gemini_client_uses_structured_output_and_parses_steps():
    captured = {}

    class FakeResponse:
        status_code = 200
        headers = {}

        @staticmethod
        def json():
            return {
                "id": "int-test",
                "status": "completed",
                "usage": {"input_tokens": 8, "output_tokens": 4, "total_tokens": 12},
                "steps": [
                    {
                        "type": "model_output",
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(
                                    {
                                        "items": [
                                            {
                                                "index": 0,
                                                "decision": "reject",
                                                "labels": [],
                                                "evidence": "健康新聞",
                                            }
                                        ]
                                    },
                                    ensure_ascii=False,
                                ),
                            }
                        ],
                    }
                ],
            }

    def fake_post(url, *, headers, json, timeout):
        captured.update(url=url, headers=headers, body=json, timeout=timeout)
        return FakeResponse()

    previous = os.environ.get("LLM_API_KEY")
    os.environ["LLM_API_KEY"] = "test-key-not-secret"
    try:
        result = gemini_topic_client.classify_batch(
            [
                {
                    "index": 0,
                    "title": "減重藥研究",
                    "content_lead": "甲" * 80,
                }
            ],
            post_fn=fake_post,
            sleep_fn=lambda _: None,
        )
    finally:
        if previous is None:
            os.environ.pop("LLM_API_KEY", None)
        else:
            os.environ["LLM_API_KEY"] = previous
    body = captured["body"]
    ok = (
        result["items"][0]["decision"] == "reject"
        and body["store"] is False
        and body["generation_config"]["thinking_level"] == "minimal"
        and body["response_format"]["mime_type"] == "application/json"
        and ("甲" * 51) not in body["input"]
    )
    return ok, (
        f"decision={result['items'][0]['decision']} "
        f"store={body['store']} structured={body['response_format']['mime_type']}"
    )


def t_gemini_client_does_not_retry_http_400():
    calls = []

    class BadResponse:
        status_code = 400
        headers = {}

    previous = os.environ.get("LLM_API_KEY")
    os.environ["LLM_API_KEY"] = "test-key-not-secret"
    try:
        try:
            gemini_topic_client.classify_batch(
                [{"index": 0, "title": "x", "content_lead": "y"}],
                post_fn=lambda *args, **kwargs: calls.append(1) or BadResponse(),
                sleep_fn=lambda _: calls.append("sleep"),
            )
            raised = False
        except gemini_topic_client.GeminiTopicError:
            raised = True
    finally:
        if previous is None:
            os.environ.pop("LLM_API_KEY", None)
        else:
            os.environ["LLM_API_KEY"] = previous
    ok = raised and calls == [1]
    return ok, f"raised={raised} calls={calls}"


TESTS = [
    t_artifact_classification_is_three_state,
    t_artifact_digest_tamper_is_detected,
    t_llm_advisory_cross_field_conflict_is_rejected,
    t_shadow_without_artifact_does_not_fake_failure_or_success,
    t_enforce_without_ready_artifact_fails_closed,
    t_enforce_empty_valid_set_fails_closed_before_model_load,
    t_enforce_ready_artifact_can_pass,
    t_gemini_batches_twenty_and_sends_only_fifty_chars,
    t_gemini_enforce_can_pass_and_failure_fails_closed,
    t_gemini_client_uses_structured_output_and_parses_steps,
    t_gemini_client_does_not_retry_http_400,
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
