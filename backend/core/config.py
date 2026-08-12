"""參數外置的單一入口（工程審查缺口 1：本檔曾是只有一行 docstring 的空殼）。

【為什麼需要】.env 載入邏輯曾在四處各刻一份（db/session、llm client、
summarize、t20_rehearsal），規則微妙不同步的風險一直在。現在集中到這裡：
- `load_env()`　從 cwd 往上找第一個 .env，只補「還沒設定」的變數（不覆蓋
  真環境變數——CI／Docker 用真 env 注入時 .env 不得搶位）
- `require(name)`　讀必填變數，缺了就給出「去哪設定」的可行動錯誤訊息
- `get(name, default)`　讀選填變數

【遷移狀態】db/session.py 已改用本模組。llm client／summarize 的私有
_load_dotenv 尚未切換（品誠的檔，等他確認後退場；行為與本模組一致，
並存不會出錯——兩者都是 setdefault 語意，先跑的生效）。

【刻意不做】不引入 pydantic-settings／python-dotenv 依賴——需求只有
「讀 KEY=VALUE」，30 行標準庫解決；也不在 import 時自動載入（顯式呼叫
load_env() 才動作，讓測試可以控制環境）。
"""
import os
from pathlib import Path

_loaded = False


def load_env(force: bool = False) -> None:
    """從 cwd 往上找第一個 .env 載入。冪等：預設只跑一次。

    只補「還沒設定」的變數（os.environ.setdefault 語意）——真環境變數
    永遠優先，Docker/CI 的注入不會被本機 .env 蓋掉。
    """
    global _loaded
    if _loaded and not force:
        return
    for folder in [Path.cwd(), *Path.cwd().parents]:
        env_file = folder / ".env"
        if env_file.is_file():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())
            break
    _loaded = True


def get(name: str, default: str | None = None) -> str | None:
    """讀選填變數（會先確保 .env 已載入）。"""
    load_env()
    return os.environ.get(name, default)


def require(name: str, hint: str = "") -> str:
    """讀必填變數；缺了就丟出「知道去哪修」的錯誤，而不是下游的 NoneType 炸鍋。"""
    value = get(name)
    if not value:
        raise RuntimeError(
            f"{name} 未設定：請在 .env 填入。" + (f"（{hint}）" if hint else "")
        )
    return value
