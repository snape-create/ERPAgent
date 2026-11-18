"""pytest 全局配置：注入各层 import 路径 + 关闭外部追踪。

三个源码目录都已随项目安装进 .venv；这里仍注入路径，
让 ``pytest`` 在未执行 uv sync 的环境下也能直接跑。
"""
import os
import sys
from pathlib import Path

# 测试不连 LangSmith：否则每个用例都会去打外网并超时 10s。
# 必须放在 import erpflow 之前；load_env(override=False) 保证这里设置的值优先于 .env。
os.environ.setdefault("LANGSMITH_TRACING", "false")
os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")

BACKEND_ROOT = Path(__file__).resolve().parents[1]

for _path in (
    BACKEND_ROOT,                              # app.* / erp_mock.*
    BACKEND_ROOT / "packages" / "harness",     # erpflow.*
    BACKEND_ROOT / "tests",                    # 测试之间互相 import
    BACKEND_ROOT.parent,                       # evals.*（评测脚本仍在仓库根）
):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
