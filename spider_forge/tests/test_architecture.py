"""模組邊界測試：防止重構後重新耦合。"""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parents[1]
STAGES_DIR = PACKAGE_DIR / "stages"
ROOT_PYTHON_FILES = {
    "__init__.py",
    "__main__.py",
    "cli.py",
    "config.py",
    "pipeline.py",
    "state.py",
}


def _tree(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def t_stages_do_not_import_other_stages():
    violations: list[str] = []
    for path in STAGES_DIR.glob("*.py"):
        for node in ast.walk(_tree(path)):
            if not isinstance(node, ast.ImportFrom):
                continue
            module = node.module or ""
            if node.level == 1 or module.startswith(
                "spider_forge.stages"
            ):
                violations.append(f"{path.name}:{node.lineno}:{module}")
    return not violations, f"violations={violations}"


def t_control_plane_has_no_crawler_import():
    violations: list[str] = []
    for path in PACKAGE_DIR.rglob("*.py"):
        if path == Path(__file__) or "_history" in path.parts or "__pycache__" in path.parts:
            continue
        for node in ast.walk(_tree(path)):
            modules = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else []
            )
            for module in modules:
                root = module.split(".", 1)[0]
                if root in {"crawler", "news_crawler"}:
                    violations.append(
                        f"{path.relative_to(PACKAGE_DIR)}:{node.lineno}:{module}"
                    )
    return not violations, f"violations={violations}"


def t_execution_entries_only_depend_on_pipeline():
    violations: list[str] = []
    for path in (
        PACKAGE_DIR / "__main__.py",
        PACKAGE_DIR / "cli.py",
        PACKAGE_DIR / "runs" / "batch.py",
        PACKAGE_DIR / "tools" / "topic_training.py",
    ):
        for node in ast.walk(_tree(path)):
            if not isinstance(node, ast.ImportFrom):
                continue
            module = node.module or ""
            if module.startswith("stages") or ".stages" in module:
                violations.append(
                    f"{path.relative_to(PACKAGE_DIR)}:{node.lineno}:{module}"
                )
    return not violations, f"violations={violations}"


def t_package_root_only_contains_public_control_modules():
    actual = {path.name for path in PACKAGE_DIR.glob("*.py")}
    unexpected = sorted(actual - ROOT_PYTHON_FILES)
    missing = sorted(ROOT_PYTHON_FILES - actual)
    return not unexpected and not missing, (
        f"unexpected={unexpected} missing={missing}"
    )


TESTS = [
    t_stages_do_not_import_other_stages,
    t_control_plane_has_no_crawler_import,
    t_execution_entries_only_depend_on_pipeline,
    t_package_root_only_contains_public_control_modules,
]
