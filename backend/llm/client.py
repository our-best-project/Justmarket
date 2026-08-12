"""LLM 供應商客戶端（T14）：Gemini Flash-Lite 與 GPT-4.1-mini 同一介面，可切換。

選型背景（06_附錄 §C）：
  - 開發期主力 = Gemini 免費層。瓶頸是 RPM（免費約 15–30/分）不是日總量,
    所以批次呼叫間 sleep 2–4 秒即可（節流在 summarize.py 的批次層做）。
  - prompt caching:兩家都對「固定前綴」自動快取（折扣只在付費層有意義）,
    只要固定的 system prompt 在前、變動報導在後,不用寫任何 code。
  - 兩家都用官方 Structured Output（Gemini responseSchema / OpenAI json_schema strict）
    保證合法 JSON,外加 prompts.validate_output() 雙保險 + 一次自動重試。

環境變數（.env 或系統環境,只設 LLM_API_KEY 時預設走 Gemini）:
  LLM_PROVIDER   gemini | openai（預設 gemini）
  LLM_API_KEY    金鑰（也可用 GEMINI_API_KEY / OPENAI_API_KEY 分開放,優先於 LLM_API_KEY）
  LLM_MODEL      覆蓋預設模型代號（預設見 DEFAULT_MODELS,以官方模型清單為準）
  LLM_BASE_URL   覆蓋 OpenAI 相容端點的 base URL（僅 provider=openai 時生效）。
                 用途:免綁卡測試走 GitHub Models（https://models.github.ai/inference,
                 模型代號要帶命名空間如 openai/gpt-4.1-mini）、或 DeepSeek 第二對照組
                 （https://api.deepseek.com,見 06_附錄 §C）。不設 = 官方 api.openai.com。

手動測一則（T13 第一站,拿樣本事件打真 API）:
    python -m backend.llm.client
"""

import json
import os
import time
from dataclasses import dataclass, field
from datetime import UTC
from pathlib import Path

import requests

from backend.llm import prompts

# 模型代號以官方清單為準,可用 .env 的 LLM_MODEL 覆蓋。
# Gemini 用 -latest 別名,免費層換版時不用改 code。
DEFAULT_MODELS = {
    "gemini": "gemini-flash-lite-latest",
    "openai": "gpt-4.1-mini",
    # Vertex 的模型代號不吃 -latest 別名,要寫實名（實測 gemini-flash-lite-latest → 404）
    "vertex": "gemini-2.5-flash-lite",
}

TEMPERATURE = 0.2       # 摘要任務要穩定,不要創意
REQUEST_TIMEOUT = 60    # 單次 HTTP 請求逾時（秒）
MAX_HTTP_RETRIES = 3    # 429/5xx 退避重試次數


class LLMError(Exception):
    """LLM 呼叫失敗（HTTP 錯誤重試耗盡、或輸出驗證重試後仍不合法）。"""


@dataclass
class LLMResult:
    """一次呼叫的結果:驗證過的欄位 + 選型比較用的量測值。"""
    data: dict                  # 已通過 validate_output 的事件欄位
    provider: str
    model: str
    latency_s: float
    input_tokens: int | None = None
    output_tokens: int | None = None
    raw_text: str = field(default="", repr=False)


def _load_dotenv() -> None:
    """輕量 .env 載入:從 cwd 往上找 .env,只補「還沒設定」的變數。

    不引入 python-dotenv 依賴;等 Nash 的 core/config.py（參數外置）就緒後,
    改由那邊統一讀取,本函式即可退場。
    """
    for folder in [Path.cwd(), *Path.cwd().parents]:
        env_file = folder / ".env"
        if env_file.is_file():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())
            return


class _BaseClient:
    """兩家共用的骨架:HTTP 重試 + 「解析→驗證→不合法自動重試一次」流程。"""

    provider = "base"

    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model

    # -- 各家要實作的兩個點 --------------------------------------------
    def _request(self, system_prompt: str, user_prompt: str, schema: dict) -> dict:
        """送出 HTTP 請求,回傳原始 response JSON。"""
        raise NotImplementedError

    def _extract(self, response: dict) -> tuple[str, int | None, int | None]:
        """從 response 取出（輸出文字, input_tokens, output_tokens）。"""
        raise NotImplementedError

    # -- 共用流程 ------------------------------------------------------
    def generate(self, system_prompt: str, user_prompt: str, schema: dict) -> LLMResult:
        """一次呼叫:Structured Output → 解析 → 驗證;不合法時帶著錯誤重試一次。"""
        prompt = user_prompt
        last_problems: list[str] = []
        for attempt in range(2):
            start = time.monotonic()
            response = self._post_with_retry(system_prompt, prompt, schema)
            latency = time.monotonic() - start
            text, tok_in, tok_out = self._extract(response)
            try:
                data = json.loads(text)
                last_problems = prompts.validate_output(data)
            except json.JSONDecodeError as exc:
                last_problems = [f"非合法 JSON: {exc}"]
                data = None
            if not last_problems:
                return LLMResult(
                    data=data, provider=self.provider, model=self.model,
                    latency_s=latency, input_tokens=tok_in,
                    output_tokens=tok_out, raw_text=text,
                )
            # 把問題附回去重試一次（Structured Output 下很少走到這裡）
            prompt = (
                user_prompt
                + "\n\n【重要】你上一次的輸出有以下問題,請修正後重新只輸出 JSON:\n- "
                + "\n- ".join(last_problems)
            )
        raise LLMError(f"{self.provider} 輸出驗證失敗（已重試 1 次）: {last_problems}")

    def _post_with_retry(self, system_prompt: str, user_prompt: str, schema: dict) -> dict:
        """429（RPM 撞牆）與 5xx 用指數退避重試,優先尊重 Retry-After。"""
        delay = 5.0
        for attempt in range(MAX_HTTP_RETRIES + 1):
            try:
                return self._request(system_prompt, user_prompt, schema)
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else 0
                if status not in (429, 500, 502, 503) or attempt == MAX_HTTP_RETRIES:
                    body = exc.response.text[:300] if exc.response is not None else ""
                    raise LLMError(f"{self.provider} HTTP {status}: {body}") from exc
                retry_after = exc.response.headers.get("Retry-After")
                wait = float(retry_after) if retry_after else delay
                time.sleep(wait)
                delay *= 2
            except requests.RequestException as exc:   # 連線層錯誤（逾時等）
                if attempt == MAX_HTTP_RETRIES:
                    raise LLMError(f"{self.provider} 連線失敗: {exc}") from exc
                time.sleep(delay)
                delay *= 2
        raise LLMError(f"{self.provider} 重試耗盡")     # 理論上到不了


class GeminiClient(_BaseClient):
    """Google Gemini（generateContent + responseSchema）。開發期免費層主力。"""

    provider = "gemini"
    BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

    # 端點與認證抽成兩個掛鉤,VertexClient 只覆寫這兩個就好——
    # 請求主體（system_instruction / responseSchema）兩邊完全相同。
    def _endpoint(self) -> str:
        return f"{self.BASE_URL}/{self.model}:generateContent"

    def _headers(self) -> dict:
        return {"x-goog-api-key": self.api_key}

    def _request(self, system_prompt: str, user_prompt: str, schema: dict) -> dict:
        resp = requests.post(
            self._endpoint(),
            headers=self._headers(),
            json={
                "system_instruction": {"parts": [{"text": system_prompt}]},
                "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
                "generationConfig": {
                    "temperature": TEMPERATURE,
                    "responseMimeType": "application/json",
                    "responseSchema": _to_gemini_schema(schema),
                },
            },
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()

    def _extract(self, response: dict) -> tuple[str, int | None, int | None]:
        text = response["candidates"][0]["content"]["parts"][0]["text"]
        usage = response.get("usageMetadata", {})
        return text, usage.get("promptTokenCount"), usage.get("candidatesTokenCount")


class VertexClient(GeminiClient):
    """Vertex AI（GCP 上的 Gemini）。與 AI Studio 同一組請求主體,只換端點與認證。

    為什麼要這條路：AI Studio 免費層卡在每日請求上限（實測跑不完佇列）,
    Vertex AI 走 GCP 帳單、由免費額度扣抵,速率上限高得多。模型品質相同。

    認證與 AI Studio 不同——不是 API key,是 OAuth access token:
      優先用 google-auth 的 ADC（會自動續期）;沒裝就退回 `gcloud auth print-access-token`。
      ⚠️ token 約 1 小時到期,而整批佇列要跑數小時,故一律快取到期前重取,
         不可只在啟動時拿一次（實測踩過:長跑到一半全部 401）。

    環境變數：
      LLM_PROVIDER=vertex
      GCP_PROJECT     GCP 專案 id（必填）
      GCP_LOCATION    預設 global——2.5 系列走 global 端點,區域端點會 404
    """

    provider = "vertex"
    TOKEN_TTL = 50 * 60          # CLI fallback 用的保守估計（主路徑以 creds.expiry 為準）

    def __init__(self, api_key: str, model: str):
        super().__init__(api_key, model)
        self.project = os.getenv("GCP_PROJECT", "")
        self.location = os.getenv("GCP_LOCATION", "global")
        if not self.project:
            raise LLMError("LLM_PROVIDER=vertex 需要 GCP_PROJECT（.env 或環境變數）")
        self._tok: str | None = None
        self._tok_until = 0.0    # 本 token 可信賴到的時刻（epoch 秒）

    def _endpoint(self) -> str:
        host = ("aiplatform.googleapis.com" if self.location == "global"
                else f"{self.location}-aiplatform.googleapis.com")
        return (f"https://{host}/v1/projects/{self.project}/locations/{self.location}"
                f"/publishers/google/models/{self.model}:generateContent")

    def _access_token(self) -> str:
        if self._tok and time.time() < self._tok_until:
            return self._tok
        token = None
        expiry_ts = None
        try:                                   # 首選：ADC,自動續期
            import google.auth
            import google.auth.transport.requests

            creds, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"])
            creds.refresh(google.auth.transport.requests.Request())
            token = creds.token
            # ⚠️ 2026-08-11 事故（253 件 401）：GCE metadata server 會快取 token，
            # refresh 拿回的可能是「再幾分鐘就過期的同一顆」。不能假設拿到就是
            # 滿血 60 分——以 creds.expiry（naive UTC）換算真實可信賴期限。
            if getattr(creds, "expiry", None) is not None:
                expiry_ts = creds.expiry.replace(tzinfo=UTC).timestamp()
        except Exception:
            import shutil
            import subprocess

            try:
                # ⚠️ 不可用 shell=True＋list（工程審查 P3-08）：POSIX 上那只會執行
                # 「gcloud」而把其餘參數丟給 shell 當位置參數——stdout 是空字串，
                # 在 Linux（GCE VM 就是）這條 fallback 等於壞的。之前沒炸只因 VM 有
                # service account、走的是上面的 google.auth 主路徑。
                # Windows 的 gcloud 是 gcloud.cmd，shutil.which 能解析到完整路徑，
                # shell=False 直接執行即可，兩平台同一條路。
                gcloud = shutil.which("gcloud")
                if not gcloud:
                    raise LLMError("找不到 gcloud CLI（不在 PATH）")
                token = subprocess.run(
                    [gcloud, "auth", "print-access-token"],
                    capture_output=True, text=True, timeout=60,
                ).stdout.strip()
            except LLMError:
                raise
            except Exception as exc:
                raise LLMError(f"取不到 GCP access token: {exc}") from exc
        if not token:
            raise LLMError("取不到 GCP access token：請確認已 gcloud auth login")
        self._tok = token
        # 主路徑用真實 expiry 留 5 分鐘邊際；CLI fallback 沒有 expiry 資訊用保守 TTL
        self._tok_until = (expiry_ts - 300) if expiry_ts else (time.time() + self.TOKEN_TTL)
        return token

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._access_token()}"}

    def _request(self, system_prompt: str, user_prompt: str, schema: dict) -> dict:
        try:
            return super()._request(system_prompt, user_prompt, schema)
        except requests.HTTPError as exc:
            # 401 兜底：就算 expiry 判斷失準（或 token 被提前撤銷），
            # 棄快取重取一次再試——不讓單顆壞 token 毀掉整批
            if exc.response is not None and exc.response.status_code == 401:
                self._tok = None
                return super()._request(system_prompt, user_prompt, schema)
            raise


class OpenAIClient(_BaseClient):
    """OpenAI（chat.completions + response_format json_schema, strict）。"""

    provider = "openai"
    URL = "https://api.openai.com/v1/chat/completions"

    @property
    def url(self) -> str:
        """OpenAI 相容端點:LLM_BASE_URL 有設就用它（GitHub Models / DeepSeek）。"""
        base = os.getenv("LLM_BASE_URL")
        return f"{base.rstrip('/')}/chat/completions" if base else self.URL

    def _request(self, system_prompt: str, user_prompt: str, schema: dict) -> dict:
        resp = requests.post(
            self.url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "temperature": TEMPERATURE,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "event_summary",
                        "strict": True,
                        "schema": schema,
                    },
                },
            },
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()

    def _extract(self, response: dict) -> tuple[str, int | None, int | None]:
        text = response["choices"][0]["message"]["content"]
        usage = response.get("usage", {})
        return text, usage.get("prompt_tokens"), usage.get("completion_tokens")


def _to_gemini_schema(schema: dict) -> dict:
    """canonical JSON Schema → Gemini responseSchema（OpenAPI 子集）。

    差異兩點:① 不支援 additionalProperties → 拿掉;
    ② 可空型別寫法不同:["string","null"] → type=string + nullable=true。
    """
    if not isinstance(schema, dict):
        return schema
    out = {}
    for key, value in schema.items():
        if key == "additionalProperties":
            continue
        if key == "type" and isinstance(value, list):
            non_null = [t for t in value if t != "null"]
            out["type"] = non_null[0] if non_null else "string"
            if "null" in value:
                out["nullable"] = True
            continue
        if key == "properties":
            out[key] = {k: _to_gemini_schema(v) for k, v in value.items()}
            continue
        if key == "items":
            out[key] = _to_gemini_schema(value)
            continue
        out[key] = value
    return out


def create_client(
    provider: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
) -> _BaseClient:
    """依環境變數（或參數覆蓋）建立客戶端。金鑰缺漏時給出可行動的錯誤訊息。"""
    _load_dotenv()
    provider = (provider or os.getenv("LLM_PROVIDER") or "gemini").lower()
    if provider not in DEFAULT_MODELS:
        raise LLMError(f"不支援的 LLM_PROVIDER: {provider}（可用: {list(DEFAULT_MODELS)}）")

    api_key = (
        api_key
        or os.getenv(f"{provider.upper()}_API_KEY")
        or os.getenv("LLM_API_KEY")
    )
    # vertex 走 OAuth（gcloud/ADC）不用金鑰,其餘兩家必須有
    if not api_key and provider != "vertex":
        raise LLMError(
            f"找不到金鑰:請在 .env 填 LLM_API_KEY（或 {provider.upper()}_API_KEY）。"
            "參考 .env.example 與 上手索引.md §3。"
        )

    model = model or os.getenv("LLM_MODEL") or DEFAULT_MODELS[provider]
    cls = {"gemini": GeminiClient, "vertex": VertexClient}.get(provider, OpenAIClient)
    return cls(api_key=api_key or "", model=model)


# ─────────────────────────────────────────────────────────────
# 手動測試（T13 第一站）:拿樣本事件打真 API,人工盯格式與分類
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    from backend.llm.samples import SAMPLE_EVENTS

    client = create_client()
    print(f"供應商: {client.provider}｜模型: {client.model}\n")

    event = SAMPLE_EVENTS[0]
    result = client.generate(
        prompts.SYSTEM_PROMPT,
        prompts.build_user_prompt(event["articles"]),
        prompts.OUTPUT_SCHEMA,
    )
    print(json.dumps(result.data, ensure_ascii=False, indent=2))
    print(
        f"\n延遲 {result.latency_s:.1f}s｜"
        f"tokens in/out: {result.input_tokens}/{result.output_tokens}"
    )
