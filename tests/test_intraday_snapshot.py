"""盘中快照落库：只在真交易日的 14:30 后采一次，且不被后续循环改写。

这份数据是拿来判断「盘中生成名单」可不可行的，它唯一的价值就是 point-in-time。
所以三条不变量必须钉死：休市日不写、14:30 前不写、同一天写第二次不覆盖第一次。
"""
import app.core.board_refresh as br
from quantcore.quant.local_store import LocalQuantStore


def _snap(updated_at: str, n: int = 5, amount: float = 1e8) -> dict:
    return {
        str(i).zfill(6): {
            "price": 10.0 + i, "open": 10.0, "high": 11.0, "low": 9.5, "prev_close": 10.0,
            "volume": 1e6, "amount": amount, "updated_at": updated_at,
            "quote_source": "tencent.realtime",
        }
        for i in range(n)
    }


def _store(tmp_path) -> LocalQuantStore:
    return LocalQuantStore(str(tmp_path / "q.sqlite"))


def test_record_writes_rows_and_never_overwrites(tmp_path):
    store = _store(tmp_path)
    assert store.record_intraday_snapshot("2026-08-05", "2026-08-05T14:30", _snap("x")) == 5
    # 同一交易日再写（比如 15:00 那轮）必须被忽略，否则 14:30 的样本会被尾盘数据污染
    later = _snap("x", amount=9e9)
    assert store.record_intraday_snapshot("2026-08-05", "2026-08-05T15:00", later) == 5
    rows = store._conn().execute(
        "SELECT captured_at, amount FROM intraday_snapshots WHERE trade_date='2026-08-05'"
    ).fetchall()
    assert {r[0] for r in rows} == {"2026-08-05T14:30"}
    assert {r[1] for r in rows} == {1e8}
    assert store.intraday_snapshot_dates() == ["2026-08-05"]


def test_record_skips_rows_without_price(tmp_path):
    store = _store(tmp_path)
    snap = _snap("x", n=2)
    snap["000000"]["price"] = 0.0  # 停牌/无报价
    assert store.record_intraday_snapshot("2026-08-05", "2026-08-05T14:30", snap) == 1


def _capture(monkeypatch, tmp_path, snapshot, today: str):
    store = _store(tmp_path)
    monkeypatch.setattr(br, "_snapshot_capture_date", None, raising=False)
    monkeypatch.setattr("quantcore.quant.local_store.get_local_store", lambda: store)

    class _Now:
        @staticmethod
        def now(_tz):
            class _D:
                @staticmethod
                def strftime(_fmt):
                    return today
            return _D()

    monkeypatch.setattr(br, "datetime", _Now)
    br._capture_intraday_snapshot(snapshot)
    return store


def test_capture_skips_stale_snapshot_on_holiday(monkeypatch, tmp_path):
    """休市日快照停在上一交易日 —— 按本机日期算会把旧收盘当成今天 14:30 写进去。"""
    store = _capture(monkeypatch, tmp_path, _snap("2026/08/04 15:00:03"), today="2026-08-05")
    assert store.intraday_snapshot_dates() == []


def test_capture_skips_before_capture_time(monkeypatch, tmp_path):
    store = _capture(monkeypatch, tmp_path, _snap("2026/08/05 10:31:00"), today="2026-08-05")
    assert store.intraday_snapshot_dates() == []


def test_capture_writes_after_capture_time(monkeypatch, tmp_path):
    store = _capture(monkeypatch, tmp_path, _snap("2026/08/05 14:31:00"), today="2026-08-05")
    assert store.intraday_snapshot_dates() == ["2026-08-05"]
    row = store._conn().execute("SELECT captured_at FROM intraday_snapshots LIMIT 1").fetchone()
    assert row[0] == "2026-08-05T14:31"


def test_capture_is_idempotent_within_a_day(monkeypatch, tmp_path):
    """后台循环每 60 秒跑一轮，14:30 之后每轮都会进来，不能每轮都去撞库。"""
    store = _capture(monkeypatch, tmp_path, _snap("2026/08/05 14:31:00"), today="2026-08-05")
    br._capture_intraday_snapshot(_snap("2026/08/05 14:32:00"))
    assert store.intraday_snapshot_dates() == ["2026-08-05"]
    assert store._conn().execute("SELECT COUNT(*) FROM intraday_snapshots").fetchone()[0] == 5


def test_optional_refresh_is_deferred_under_memory_pressure(monkeypatch):
    monkeypatch.setenv("BOARD_REFRESH_MIN_AVAILABLE_MB", "3072")

    class _Memory:
        available = 2048 * 1024 * 1024

    monkeypatch.setattr(br.psutil, "virtual_memory", lambda: _Memory())
    assert br._has_memory_budget("risk-scan") is False


def test_panel_batch_isolates_each_symbol(monkeypatch):
    calls = []

    class _Process:
        exitcode = 0

        def __init__(self, *, target, args):
            calls.append((target, args))

        def start(self):
            pass

        def join(self, _timeout):
            pass

        def is_alive(self):
            return False

        def close(self):
            pass

    class _Context:
        Process = _Process

    monkeypatch.setattr(br.multiprocessing, "get_context", lambda _method: _Context())
    br._run_panel_batch_isolated("2026-08-24", ["600001", "600002"])

    assert [args for _target, args in calls] == [
        ("2026-08-24", ["600001"]),
        ("2026-08-24", ["600002"]),
    ]


def test_panel_worker_can_spawn_with_an_isolated_database(monkeypatch, tmp_path):
    monkeypatch.setenv("QUANT_DATA_DB_PATH", str(tmp_path / "worker.sqlite"))
    context = br.multiprocessing.get_context("spawn")
    process = context.Process(target=br._panel_batch_worker, args=("2099-01-01", []))
    process.start()
    process.join(30)
    assert process.is_alive() is False
    assert process.exitcode == 0
    process.close()
