"""测试全局隔离：任何测试都不许碰生产量化库。

2026-08-06 抓到的事故：`test_smart_pool_saas.py` 每跑一次就往
`runtime/quant_data.sqlite` 写 21 行占位数据（股1/600001 这种，3 个池 × 7 只），
因为被测代码里的留痕走 `get_local_store()` 全局单例，而单例默认指向生产库。
连续几天跑测试之后，首页「智能选股」卡片读 `latest_picks` 读到的就是「股1 84分」。

修在这里而不是改那一个测试文件：下次换个测试碰到同一条留痕路径还会再犯。
`DEFAULT_DB_PATH` 在 import 时读 `QUANT_DATA_DB_PATH`，所以必须在导入任何
quantcore 模块**之前**改环境变量 —— conftest 的模块级代码正好在收集用例前执行。

要在测试里读真实生产数据（一般不该有），显式传 db_path 给 LocalQuantStore。
"""
import os
import tempfile
from pathlib import Path

_TEST_DB_DIR = Path(tempfile.gettempdir()) / "astockpick-test-db"
_TEST_DB_DIR.mkdir(parents=True, exist_ok=True)

# 必须早于任何 `from quantcore...` 的导入
os.environ["QUANT_DATA_DB_PATH"] = str(_TEST_DB_DIR / "quant_data.sqlite")
os.environ.setdefault("QUANT_DATA_DIR", str(_TEST_DB_DIR))
os.environ.setdefault("SAAS_LITE_DB_PATH", str(_TEST_DB_DIR / "lite.sqlite"))

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _guard_production_db():
    """每个用例都断言一次：单例确实没指向生产库。

    断言而不是只设环境变量 —— 万一将来有人在 import 期就实例化了单例，
    或者路径解析逻辑改了，这条会立刻失败，而不是等到某天首页又冒出「股1」。
    """
    from quantcore.quant.local_store import get_local_store

    path = str(get_local_store().db_path)
    assert "runtime" not in path.replace(str(_TEST_DB_DIR), ""), (
        f"测试正在使用生产量化库：{path}"
    )
    yield
