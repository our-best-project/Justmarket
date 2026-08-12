"""候選程式的沙盒執行服務。

MVP 護欄（docs/spec/08 §2、§關鍵設計決策）：
  1. timeout —— 跨平台，硬上限（本檔實作、已驗證）。
  2. py_compile 前置檢查 —— 執行前先擋語法錯，省一輪執行（§R3）。
  3. 網域白名單 —— 以 env `SPIDERFORGE_ALLOWED_DOMAINS` 注入，由 Scrapy 端
     （spider 的 allowed_domains / 未來下載中介層）落實。
  4. CPU/記憶體上限 —— POSIX 用 resource.setrlimit；Windows 無 resource 模組。

⚠️ 平台誠實標註：Windows 上第 4 道只有 timeout 生效（記憶體硬上限需 Job Object，
   列 TODO）。要跑真正不受信任的程式碼，升級 Docker+gVisor（§3.4 升級路徑）。
   本場景是「自家流程從自訂 prompt 產的爬蟲」，風險等級不到那裡。
"""

from __future__ import annotations

import json
import os
import py_compile
import shlex
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

IS_POSIX = os.name == "posix"


@dataclass
class SandboxResult:
    exit_code: int | None      # None = timeout
    stdout: str
    stderr: str
    timed_out: bool

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


# 候選碼只能拿到這些「非機密」環境變數（remediation P0-3）。
# ⚠️ 絕不含 DATABASE_URL / DEEPSEEK_API / KIMI_API / OLLAMA_HOST 等 secrets。
_SANDBOX_ENV_PASSTHROUGH = (
    "PATH", "SYSTEMROOT", "SYSTEMDRIVE", "TEMP", "TMP",
    "NUMBER_OF_PROCESSORS", "COMSPEC",
    # Scrapy 2.17 啟動時會用 Path("~/.config").expanduser()；Windows 子程序若
    # 沒有 USERPROFILE/HOMEDRIVE/HOMEPATH 會在下載前直接 RuntimeError。
    "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA",
)


def sandbox_env(allowed_domains: list[str] | None = None) -> dict[str, str]:
    """建構沙盒子程序的環境：只白名單非機密變數 + 強制 UTF-8 + 網域白名單。

    ⚠️ 誠實標註：這只擋「候選碼順手讀 os.environ 拿 secrets」。它**不是**完整
    OS 隔離——subprocess+timeout 不是安全邊界，網路/檔案系統/記憶體隔離在 Windows 上
    未驗證（UNVERIFIED）。真要跑不受信任程式碼需 Docker+gVisor / microVM。
    """
    env = {k: os.environ[k] for k in _SANDBOX_ENV_PASSTHROUGH if k in os.environ}
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["SPIDERFORGE_SANDBOX"] = "1"
    env["SPIDERFORGE_ALLOWED_DOMAINS"] = ",".join(allowed_domains or [])
    return env


def run_scrapy_crawl(
    project_dir: str,
    spider_name: str,
    *,
    out_json: str,
    allowed_domains: list[str] | None = None,
    timeout_s: int = 180,
    item_limit: int = 8,
    python_exe: str | None = None,
) -> SandboxResult:
    """在 Scrapy 專案目錄跑 `scrapy crawl`，item 匯出到 out_json（-O 覆寫）。

    SPIDERFORGE_SANDBOX=1 → settings.py 停用 Neon pipeline + 啟用網域白名單中介層。
    CLOSESPIDER_ITEMCOUNT 限制抓取量。env 只帶非機密變數（見 sandbox_env）。
    """
    # ⚠️ Windows 繁中系統預設 cp950；不強制 UTF-8 會在讀 scrapy 的中文輸出時
    #    在 subprocess reader thread 崩潰（UnicodeDecodeError），把真錯誤蒙掉。
    env = sandbox_env(allowed_domains)
    cmd = [
        python_exe or sys.executable, "-X", "utf8", "-m", "scrapy", "crawl", spider_name,
        "-O", out_json,
        "-s", f"CLOSESPIDER_ITEMCOUNT={item_limit}",
        "-L", "INFO",
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout_s, cwd=project_dir, env=env,
        )
        return SandboxResult(proc.returncode, proc.stdout, proc.stderr, False)
    except subprocess.TimeoutExpired as e:
        return SandboxResult(
            None, e.stdout or "",
            (e.stderr or "") + f"\n[sandbox] timeout after {timeout_s}s", True,
        )


def run_candidate(
    candidate_path: str,
    *,
    out_json: str,
    allowed_domains: list[str] | None = None,
    timeout_s: int = 180,
    item_limit: int = 8,
    python_exe: str | None = None,
) -> SandboxResult:
    """用 `scrapy runspider <檔案>` 跑隔離區的候選 spider（不需在 SPIDER_MODULES 內）。

    候選檔必須自包含，工作目錄就是候選檔所在目錄；env 只帶非機密變數
    （sandbox_env）。候選跑在隔離區，active spider 完全不受影響（P0-2）。
    """
    runtime = os.environ.get("SPIDERFORGE_CRAWLER_RUNTIME", "local").strip().lower()
    if runtime == "docker":
        return _run_candidate_docker(
            candidate_path,
            out_json=out_json,
            allowed_domains=allowed_domains,
            timeout_s=timeout_s,
            item_limit=item_limit,
        )
    if runtime != "local":
        return SandboxResult(
            2,
            "",
            f"unsupported SPIDERFORGE_CRAWLER_RUNTIME={runtime!r}",
            False,
        )

    env = sandbox_env(allowed_domains)
    candidate = Path(candidate_path).resolve()
    cmd = [
        python_exe or sys.executable, "-X", "utf8", "-m", "scrapy", "runspider",
        str(candidate), "-O", out_json,
        "-s", f"CLOSESPIDER_ITEMCOUNT={item_limit}",
        "-L", "INFO",
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout_s, cwd=str(candidate.parent), env=env,
        )
        return SandboxResult(proc.returncode, proc.stdout, proc.stderr, False)
    except subprocess.TimeoutExpired as e:
        return SandboxResult(
            None, e.stdout or "",
            (e.stderr or "") + f"\n[sandbox] timeout after {timeout_s}s", True,
        )


def run_fixture_candidate(
    candidate_path: str,
    fixture: dict,
    *,
    allowed_domains: list[str] | None = None,
    timeout_s: int = 60,
    python_exe: str | None = None,
) -> SandboxResult:
    """以保存的 response 執行候選 callback；不觸碰正式網站。

    控制層只傳 JSON bundle。候選模組的 import 與 callback 都在 crawler runtime
    子程序內發生，不會載入 Spider Forge 控制程序。
    """
    candidate = Path(candidate_path).resolve()
    if not candidate.is_file():
        return SandboxResult(2, "", f"candidate_not_found:{candidate}", False)
    bundle = json.dumps(
        {
            "spider_code": candidate.read_text(encoding="utf-8"),
            "fixture": fixture,
        },
        ensure_ascii=False,
    )
    runtime = os.environ.get(
        "SPIDERFORGE_CRAWLER_RUNTIME", "local"
    ).strip().lower()
    if runtime == "docker":
        return _run_fixture_candidate_docker(
            bundle,
            allowed_domains=allowed_domains,
            timeout_s=timeout_s,
        )
    if runtime != "local":
        return SandboxResult(
            2,
            "",
            f"unsupported SPIDERFORGE_CRAWLER_RUNTIME={runtime!r}",
            False,
        )

    # shared → spider_forge → <repo>。只當作子程序的工作目錄，不 import
    # （不得 import 是 test_architecture.py 守著的架構不變式）。
    crawler = Path(__file__).resolve().parents[2] / "crawler"
    env = sandbox_env(allowed_domains)
    cmd = [
        python_exe or sys.executable,
        "-X",
        "utf8",
        "-m",
        "news_crawler.fixture_runner",
    ]
    try:
        proc = subprocess.run(
            cmd,
            input=bundle,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            cwd=str(crawler),
            env=env,
        )
        return SandboxResult(
            proc.returncode, proc.stdout, proc.stderr, False
        )
    except subprocess.TimeoutExpired as exc:
        return SandboxResult(
            None,
            exc.stdout or "",
            (exc.stderr or "")
            + f"\n[fixture sandbox] timeout after {timeout_s}s",
            True,
        )


def _run_fixture_candidate_docker(
    bundle: str,
    *,
    allowed_domains: list[str] | None,
    timeout_s: int,
) -> SandboxResult:
    """在無網路 crawler container 內執行 fixture runner。"""
    docker_prefix = _docker_command_prefix()
    image = os.environ.get(
        "SPIDERFORGE_CRAWLER_IMAGE", "stock-platform-crawler:local"
    )
    container_name = f"spiderforge-fixture-{uuid.uuid4().hex[:12]}"
    domains = ",".join(allowed_domains or [])
    docker_env = {
        key: os.environ[key]
        for key in ("PATH", "SYSTEMROOT", "SYSTEMDRIVE", "COMSPEC")
        if key in os.environ
    }
    cmd = [
        *docker_prefix,
        "run",
        "--rm",
        "--name",
        container_name,
        "--interactive",
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        "128",
        "--memory",
        "1g",
        "--cpus",
        "1",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,size=256m",
        "--env",
        "SPIDERFORGE_SANDBOX=1",
        "--env",
        f"SPIDERFORGE_ALLOWED_DOMAINS={domains}",
        "--entrypoint",
        "python",
        image,
        "-m",
        "news_crawler.fixture_runner",
    ]
    try:
        proc = subprocess.run(
            cmd,
            input=bundle,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            env=docker_env,
        )
        return SandboxResult(
            proc.returncode, proc.stdout, proc.stderr, False
        )
    except FileNotFoundError:
        return SandboxResult(
            127,
            "",
            f"docker_runtime_unavailable:{' '.join(docker_prefix)}",
            False,
        )
    except subprocess.TimeoutExpired as exc:
        subprocess.run(
            [*docker_prefix, "rm", "-f", container_name],
            capture_output=True,
            timeout=15,
            env=docker_env,
        )
        return SandboxResult(
            None,
            exc.stdout or "",
            (exc.stderr or "")
            + f"\n[fixture docker] timeout after {timeout_s}s",
            True,
        )


def _run_candidate_docker(
    candidate_path: str,
    *,
    out_json: str,
    allowed_domains: list[str] | None,
    timeout_s: int,
    item_limit: int,
) -> SandboxResult:
    """以獨立 crawler image 執行 candidate；程式走 stdin，JSON 走 stdout。

    stdin 避免控制面本身也在 container 時，candidate bind path 對 host Docker daemon
    不可見。子 container 只把程式寫入 tmpfs，不取得 host env、專案原始碼、DB、
    Docker socket 或 LLM secrets。
    """
    candidate = Path(candidate_path).resolve()
    output = Path(out_json).resolve()
    if not candidate.is_file():
        return SandboxResult(2, "", f"candidate_not_found:{candidate}", False)
    output.parent.mkdir(parents=True, exist_ok=True)

    docker_prefix = _docker_command_prefix()
    image = os.environ.get(
        "SPIDERFORGE_CRAWLER_IMAGE", "stock-platform-crawler:local"
    )
    container_name = f"spiderforge-{uuid.uuid4().hex[:12]}"
    domains = ",".join(allowed_domains or [])
    docker_env = {
        key: os.environ[key]
        for key in ("PATH", "SYSTEMROOT", "SYSTEMDRIVE", "COMSPEC")
        if key in os.environ
    }
    cmd = [
        *docker_prefix,
        "run",
        "--rm",
        "--name",
        container_name,
        "--interactive",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        "128",
        "--memory",
        "1g",
        "--cpus",
        "1",
        "--shm-size",
        "512m",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,size=256m",
        "--env",
        "SPIDERFORGE_SANDBOX=1",
        "--env",
        f"SPIDERFORGE_ALLOWED_DOMAINS={domains}",
        "--entrypoint",
        "/bin/sh",
        image,
        "-c",
        (
            "cat > /tmp/spider.py && "
            "python -m scrapy runspider /tmp/spider.py "
            "-O -:json "
            f"-s CLOSESPIDER_ITEMCOUNT={item_limit} -L INFO"
        ),
    ]
    try:
        proc = subprocess.run(
            cmd,
            input=candidate.read_text(encoding="utf-8"),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            env=docker_env,
        )
        if proc.returncode == 0:
            try:
                output.write_text(proc.stdout, encoding="utf-8")
            except OSError as exc:
                return SandboxResult(
                    1, "", f"docker_output_write_failed:{exc}", False
                )
        return SandboxResult(proc.returncode, "", proc.stderr, False)
    except FileNotFoundError:
        return SandboxResult(
            127,
            "",
            f"docker_runtime_unavailable:{' '.join(docker_prefix)}",
            False,
        )
    except subprocess.TimeoutExpired as exc:
        subprocess.run(
            [*docker_prefix, "rm", "-f", container_name],
            capture_output=True,
            timeout=15,
            env=docker_env,
        )
        return SandboxResult(
            None,
            exc.stdout or "",
            (exc.stderr or "") + f"\n[docker sandbox] timeout after {timeout_s}s",
            True,
        )


def _docker_command_prefix() -> list[str]:
    """選 Docker CLI；Windows 無 native CLI 時自動使用 WSL Docker。"""
    configured = os.environ.get("SPIDERFORGE_DOCKER_BIN", "").strip()
    if configured:
        return shlex.split(configured, posix=os.name != "nt")
    if os.name == "nt" and not shutil.which("docker") and shutil.which("wsl.exe"):
        return ["wsl.exe", "docker"]
    return ["docker"]


def syntax_check(script_path: str) -> tuple[bool, str]:
    """執行前的靜態護欄與 py_compile。回 (通過?, 錯誤訊息)。"""
    try:
        source = Path(script_path).read_text(encoding="utf-8")
    except OSError as exc:
        return False, f"candidate_read_error: {exc}"
    line_count = len(source.splitlines())
    if line_count > 350:
        return False, f"candidate_too_long: {line_count} lines (max 350)"
    if ".first(" in source:
        return False, (
            "scrapy_selectorlist_first_invalid: SelectorList has no .first(); "
            "use .get(), getall(), or [0] after a truth check"
        )
    try:
        py_compile.compile(script_path, doraise=True)
        return True, ""
    except py_compile.PyCompileError as e:
        return False, str(e)


def _posix_limits(cpu_seconds: int, mem_mb: int):
    import resource  # POSIX only

    def _apply() -> None:
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        mem = mem_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (mem, mem))

    return _apply


def run_script(
    script_path: str,
    *,
    args: list[str] | None = None,
    allowed_domains: list[str] | None = None,
    timeout_s: int = 120,
    cpu_seconds: int = 60,
    mem_mb: int = 1024,
    cwd: str | None = None,
    python_exe: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> SandboxResult:
    """在 subprocess 沙盒執行腳本。預設**不帶 DATABASE_URL**——沙盒測試階段
    不該寫正式庫；要寫測試庫由呼叫端經 extra_env 明確帶入。
    """
    env = {
        "PATH": os.environ.get("PATH", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),  # Windows 執行 python 需要
        "USERPROFILE": os.environ.get("USERPROFILE", ""),
        "HOMEDRIVE": os.environ.get("HOMEDRIVE", ""),
        "HOMEPATH": os.environ.get("HOMEPATH", ""),
        "APPDATA": os.environ.get("APPDATA", ""),
        "LOCALAPPDATA": os.environ.get("LOCALAPPDATA", ""),
        "SPIDERFORGE_ALLOWED_DOMAINS": ",".join(allowed_domains or []),
        "SPIDERFORGE_SANDBOX": "1",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
    }
    if extra_env:
        env.update(extra_env)

    cmd = [python_exe or sys.executable, "-X", "utf8", script_path, *(args or [])]
    preexec = _posix_limits(cpu_seconds, mem_mb) if IS_POSIX else None

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            cwd=cwd,
            env=env,
            preexec_fn=preexec,  # Windows: None（忽略）
        )
        return SandboxResult(proc.returncode, proc.stdout, proc.stderr, False)
    except subprocess.TimeoutExpired as e:
        return SandboxResult(
            None,
            e.stdout or "",
            (e.stderr or "") + f"\n[sandbox] timeout after {timeout_s}s",
            True,
        )

from ..config import run_dir
from ..state import SpiderForgeState


def sandbox_test(state: SpiderForgeState) -> dict:
    from ..output import artifacts

    name = state["source_prefix"]
    run_id = state.get("run_id") or uuid.uuid4().hex[:8]
    candidate = artifacts.write_candidate(run_id, name, state.get("spider_code", ""))
    ok, error = syntax_check(str(candidate))
    if not ok:
        return {
            "test_result": {
                "passed": False,
                "stage": "syntax",
                "error": error,
                "item_count": 0,
            },
            "candidate_path": str(candidate),
            "run_id": run_id,
            "status": "validating",
        }

    output_dir = run_dir(run_id)
    out_path = output_dir / "items.json"
    stdout_log = output_dir / "sandbox.stdout.log"
    stderr_log = output_dir / "sandbox.stderr.log"
    out_path.unlink(missing_ok=True)
    result = run_candidate(
        str(candidate),
        out_json=str(out_path),
        python_exe=sys.executable,
        allowed_domains=state.get("validation", {}).get("allowed_domains"),
        item_limit=int(state.get("constraints", {}).get("validation_probe_items", 20)),
    )
    items = []
    try:
        if out_path.exists() and out_path.stat().st_size:
            items = json.loads(out_path.read_text(encoding="utf-8"))
    except Exception:
        items = []
    stdout_log.write_text(result.stdout or "", encoding="utf-8")
    stderr_log.write_text(result.stderr or "", encoding="utf-8")
    return {
        "test_result": {
            "passed": result.ok,
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "stderr_tail": (result.stderr or "")[-2500:],
            "stdout_tail": (result.stdout or "")[-1000:],
            "item_count": len(items),
            "output_path": str(out_path),
            "stdout_log": str(stdout_log),
            "stderr_log": str(stderr_log),
        },
        "candidate_path": str(candidate),
        "run_id": run_id,
        "status": "validating",
    }


# ════════════════════════ content-vs-block gate（spec v2 §3.3，D3）════════════════════════

# 整批抓到的「文章」其實是挑戰/錯誤/空殼頁的字樣（比 validators 的 per-item soft-block
# 再寬一些，含 404/5xx 與樣板）。命中比例高，或內容整批雷同，就判 block_page_200。
