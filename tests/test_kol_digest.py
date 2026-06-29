"""KOL 采集器离线单测：按路径加载 scripts/build_kol_digest.py，不联网、不调 LLM。"""
import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_kol_digest.py"


def _load_bkd():
    spec = importlib.util.spec_from_file_location("build_kol_digest", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bkd = _load_bkd()


def test_module_loads():
    assert hasattr(bkd, "collect")
