"""架构边界守卫：harness 层禁止依赖应用层。

依赖方向是单向的：

    app.*      →  erpflow.*     允许
    erpflow.*  →  app.*         禁止

harness 是可复用的 Agent 核心（换一个 ERP 只需改 integrations/erp），
一旦它 import app（FastAPI 路由、Mongo 仓储、mock ERP），就会和这个具体应用绑死。
本测试用 AST 静态扫描强制这条规则，CI 里失败即阻断合并。
"""
import ast
from pathlib import Path

HARNESS_ROOT = Path(__file__).resolve().parents[1] / "packages" / "harness" / "erpflow"

# 任何指向应用层/模拟器的 import 都是违规
BANNED_PREFIXES = ("app.", "app", "erp_mock", "erp_mock.")


def _imported_modules(path: Path) -> list[tuple[str, int]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            # 相对 import 不会跨出 harness 包边界，忽略
            if node.level or node.module is None:
                continue
            found.append((node.module, node.lineno))
    return found


def test_harness_never_imports_app_layer():
    violations: list[str] = []
    for py_file in sorted(HARNESS_ROOT.rglob("*.py")):
        for module, lineno in _imported_modules(py_file):
            if module in BANNED_PREFIXES or any(
                module.startswith(prefix) for prefix in ("app.", "erp_mock.")
            ):
                rel = py_file.relative_to(HARNESS_ROOT.parent)
                violations.append(f"{rel}:{lineno} imports {module}")

    assert not violations, (
        "harness 层禁止依赖 app 层（依赖方向必须是 app → erpflow）：\n"
        + "\n".join(violations)
    )
