"""讓 Crawler Runtime 既有 ``TESTS`` 檢查可由 pytest 執行。"""

from __future__ import annotations

from collections.abc import Callable
from importlib import import_module
from pathlib import Path
from pkgutil import iter_modules

import pytest

Check = Callable[[], tuple[bool, str]]
_HERE = Path(__file__).resolve().parent
_PACKAGE = __package__


def _collect_checks() -> list[tuple[str, Check]]:
    checks: list[tuple[str, Check]] = []
    current_module = Path(__file__).stem
    for module_info in iter_modules([str(_HERE)]):
        module_name = module_info.name
        if not module_name.startswith("test_") or module_name == current_module:
            continue
        module = import_module(f"{_PACKAGE}.{module_name}")
        for check in getattr(module, "TESTS", ()):
            checks.append((f"{module_name}.{check.__name__}", check))
    return checks


CHECKS = _collect_checks()


@pytest.mark.parametrize(
    ("case_id", "check"),
    CHECKS,
    ids=[case_id for case_id, _ in CHECKS],
)
def test_existing_check(case_id: str, check: Check) -> None:
    result = check()
    assert isinstance(result, tuple) and len(result) == 2, (
        f"{case_id} 必須回傳 (是否通過, 說明)，實際為 {result!r}"
    )
    passed, detail = result
    assert passed, detail
